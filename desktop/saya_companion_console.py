"""Saya 桌面 Companion Console（2026-09 方案第二节：分类面板重构）。

三栏布局：
- 左栏  统一对话：与网页 / Minecraft 共享同一 ConversationService、人格、
        长期记忆与会话历史（GET/POST /api/bridge/minecraft/chat）。
- 中栏  Minecraft 实时状态：WebSocket /api/bridge/minecraft/ws 服务器推送，
        另有 5 秒兜底轮询；含当前计划（步骤/缺料/等待）显示。
- 右栏  控制台（QToolBox 分类面板，由统一 CommandCatalog 驱动）：
        常用 / 移动 / 采集 / 制作 / 背包 / 战斗 / 高级。
        需要参数的命令都是「输入框+按钮」面板，不是固定按钮堆。
        原始命令只在「高级」页，doctor 来源直发（允许 /raw）。

运行：python desktop/saya_companion_console.py [http://127.0.0.1:8000]
依赖：PySide6（pip install PySide6），后端为 server5-22B----.py。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Fallback: the repo can ship a pip --target install under desktop/vendor so
# the console runs without polluting the interpreter environment.
try:
    import PySide6  # noqa: F401
except ImportError:
    _vendor = Path(__file__).resolve().parent / "vendor"
    if _vendor.is_dir():
        sys.path.insert(0, str(_vendor))
    import PySide6  # noqa: F401

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QToolBox,
    QAbstractItemView, QFileDialog, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

try:  # QtWebSockets ships inside the PySide6 wheel; no extra dependency.
    from PySide6.QtWebSockets import QWebSocket
except ImportError:  # pragma: no cover
    QWebSocket = None

DEFAULT_BASE = os.environ.get("SAYA_CONSOLE_URL", "http://127.0.0.1:8000").rstrip("/")
API = "/api/bridge/minecraft"
LOG_TAIL = 80

# Curated mining targets for the collect panel (kept small on purpose; the
# full item registry backs the crafting search instead).
COLLECT_BLOCKS = [
    ("圆石（挖石头）", "cobblestone"), ("石头", "stone"), ("泥土", "dirt"), ("沙子", "sand"),
    ("砂岩", "sandstone"), ("砂砾", "gravel"), ("黏土", "clay"),
    ("煤矿石", "coal_ore"), ("铁矿石", "iron_ore"), ("铜矿石", "copper_ore"),
    ("金矿石", "gold_ore"), ("红石矿石", "redstone_ore"),
    ("青金石矿石", "lapis_ore"), ("绿宝石矿石", "emerald_ore"), ("钻石矿石", "diamond_ore"),
    ("下界岩", "netherrack"), ("黑曜石", "obsidian"), ("雪块", "snow_block"),
]


class CompanionConsole(QMainWindow):
    def __init__(self, base_url):
        super().__init__()
        self.base = base_url.rstrip("/")
        self.setWindowTitle("Saya Companion Console")
        self.resize(1360, 800)
        self._nam = QNetworkAccessManager(self)
        self._ws = None
        self._last_state = {}
        self._catalog = {"commands": [], "categories": {}}
        self._items = []          # item registry view for the crafting search
        self._last_plan = {}
        self._last_conversation_at = None
        self._build_ui()
        self._open_ws()
        self._refresh_status()
        self._load_history()
        self._refresh_sessions()
        self._load_catalog()
        poll = QTimer(self, interval=5000, timeout=self._refresh_status)
        poll.start()
        plan_poll = QTimer(self, interval=5000, timeout=self._refresh_plan)
        plan_poll.start()
        history_poll = QTimer(self, interval=5000, timeout=self._load_history)
        history_poll.start()
        if QWebSocket is not None:
            ws_watch = QTimer(self, interval=5000, timeout=self._open_ws)
            ws_watch.start()

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_chat_panel())
        splitter.addWidget(self._build_state_panel())
        splitter.addWidget(self._build_control_panel())
        splitter.setSizes([420, 330, 520])

        top = QHBoxLayout()
        self.base_input = QLineEdit(self.base)
        self.base_input.setMinimumWidth(240)
        top.addWidget(QLabel("后端地址"))
        top.addWidget(self.base_input)
        btn_reapply = QPushButton("应用并重连")
        btn_reapply.clicked.connect(self._reapply_base)
        top.addWidget(btn_reapply)
        self.ws_label = QLabel("WebSocket：未连接")
        top.addWidget(self.ws_label)
        top.addStretch(1)

        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(top)
        root.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

    def _build_chat_panel(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        header = QHBoxLayout()
        header.addWidget(QLabel("统一对话（网页 / 游戏 / 桌面共享同一记忆）"))
        btn_history = QPushButton("刷新历史")
        btn_history.clicked.connect(self._load_history)
        header.addWidget(btn_history)
        layout.addLayout(header)

        # Current conversation + optional historical transcript: viewing an
        # old game session splits the area in half; closing history gives the
        # current conversation the full size again.
        self.chat_splitter = QSplitter(Qt.Vertical)
        self.history_view = QPlainTextEdit(readOnly=True)
        self.history_view.setPlaceholderText("选择下方一个游戏会话并点击「查看」，这里会显示那次对话")
        self.history_view.hide()
        self.chat_view = QPlainTextEdit(readOnly=True)
        self.chat_view.setPlaceholderText("对话历史加载中…")
        self.chat_splitter.addWidget(self.history_view)
        self.chat_splitter.addWidget(self.chat_view)
        self.chat_splitter.setSizes([1, 1])
        layout.addWidget(self.chat_splitter, 1)

        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("游戏会话（按世界加入划分）"))
        btn_refresh_sessions = QPushButton("刷新")
        btn_refresh_sessions.clicked.connect(self._refresh_sessions)
        session_row.addWidget(btn_refresh_sessions)
        session_row.addStretch(1)
        layout.addLayout(session_row)

        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.session_list.setToolTip(
            "每次加入世界都是一条独立记录；按住 Ctrl 可多选同世界的多条会话进行合并"
        )
        self.session_list.setMaximumHeight(110)
        layout.addWidget(self.session_list)

        session_btn_row = QHBoxLayout()
        btn_view_session = QPushButton("查看")
        btn_view_session.setToolTip("在上方显示该次对话；当前对话缩为一半")
        btn_view_session.clicked.connect(self._view_selected_session)
        btn_merge_sessions = QPushButton("合并所选")
        btn_merge_sessions.setToolTip("把同一世界的多条会话按时间合并为一条（至少选两条）")
        btn_merge_sessions.clicked.connect(self._merge_selected_sessions)
        btn_archive_session = QPushButton("归档到主记忆")
        btn_archive_session.setToolTip("AI 归纳该次对话后写入主记忆（episodic）")
        btn_archive_session.clicked.connect(self._archive_selected_session)
        btn_close_history = QPushButton("关闭历史")
        btn_close_history.clicked.connect(self._close_history)
        session_btn_row.addWidget(btn_view_session)
        session_btn_row.addWidget(btn_merge_sessions)
        session_btn_row.addWidget(btn_archive_session)
        session_btn_row.addWidget(btn_close_history)
        session_btn_row.addStretch(1)
        layout.addLayout(session_btn_row)

        row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("对 Saya 说话（Enter 发送；复杂目标会自动规划）")
        self.chat_input.returnPressed.connect(self._send_chat)
        self.chat_cmd_mode = QCheckBox("按命令发送")
        btn_send = QPushButton("发送")
        btn_send.clicked.connect(self._send_chat)
        row.addWidget(self.chat_input, 1)
        row.addWidget(self.chat_cmd_mode)
        row.addWidget(btn_send)
        layout.addLayout(row)
        return box

    def _build_state_panel(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.addWidget(QLabel("Minecraft 实时状态"))

        switches = QGroupBox("持续状态开关")
        switch_rows = QVBoxLayout(switches)
        switch_row = QHBoxLayout()
        self.follow_switch = QCheckBox("持续跟随")
        self.follow_switch.clicked.connect(self._set_follow_enabled)
        self.auto_discard_switch = QCheckBox("自动交还非任务拾取物")
        self.auto_discard_switch.clicked.connect(self._set_auto_discard_enabled)
        switch_row.addWidget(self.follow_switch)
        switch_row.addWidget(self.auto_discard_switch)
        switch_rows.addLayout(switch_row)
        combat_row = QHBoxLayout()
        self.auto_attack_switch = QCheckBox("自动攻击敌对生物")
        self.auto_hunt_switch = QCheckBox("自动狩猎动物")
        self.auto_counter_switch = QCheckBox("受到攻击时反击")
        self.counter_owner_switch = QCheckBox("反击主人")
        self.auto_attack_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("auto_attack_hostiles", enabled)
        )
        self.auto_hunt_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("auto_hunt_animals", enabled)
        )
        self.auto_counter_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("auto_counterattack", enabled)
        )
        self.counter_owner_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("counterattack_owner", enabled)
        )
        for widget in (
            self.auto_attack_switch, self.auto_hunt_switch,
            self.auto_counter_switch, self.counter_owner_switch,
        ):
            combat_row.addWidget(widget)
        switch_rows.addLayout(combat_row)
        mining_row = QHBoxLayout()
        self.auto_mine_switch = QCheckBox("自动挖掘矿石")
        self.auto_mine_switch.setToolTip(
            "总开关：空闲时每 20 秒挖一处下方已启用矿种的矿石；跟随/任务/战斗/紧急状态期间自动让路。"
            "每种矿单独启用并填识别距离；跟随移动中实际距离自动压缩到 6 格，只挖顺手矿不打断走路。"
            "不启用任何矿种则不会挖。"
        )
        self.auto_mine_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("auto_mine_ores", enabled)
        )
        mining_row.addWidget(self.auto_mine_switch)
        mining_row.addWidget(QLabel("默认识别距离"))
        self.auto_mine_range = QSpinBox()
        self.auto_mine_range.setRange(4, 64)
        self.auto_mine_range.setSuffix(" 格")
        self.auto_mine_range.setToolTip(
            "所有矿种识别距离的默认值；下方每种矿可单独填写距离覆盖它。"
            "开启持续跟随时实际距离压缩到 6 格以免打断走路"
        )
        self.auto_mine_range.editingFinished.connect(self._set_auto_mine_default_range)
        mining_row.addWidget(self.auto_mine_range)
        # SLU backend: the fine-tuned local understanding model (in-process).
        self.slu_local_switch = QCheckBox("本地模型理解（微调 SLU）")
        self.slu_local_switch.setToolTip(
            "开启后，游戏里/对话里的话语先由本地微调小模型判断是任务还是闲聊并提取目标，"
            "命中任务则直接执行（无需云端 API）；判断为闲聊仍由大模型人格回复。"
            "任何失败自动回落到原有流程。需已配置 slu_local_model 模型目录。"
        )
        self.slu_local_switch.clicked.connect(
            lambda enabled: self._set_config_toggle("slu_backend", "local" if enabled else "legacy")
        )
        mining_row.addWidget(self.slu_local_switch)
        mining_row.addStretch(1)
        switch_rows.addLayout(mining_row)
        # Per-ore modes: one enable checkbox + one fillable detection distance
        # per ore group - "自动挖掘铁矿 / 自动挖掘钻石" are independent modes.
        ore_grid = QGridLayout()
        ore_grid.setHorizontalSpacing(4)
        self.auto_mine_group_checks = {}
        self.auto_mine_range_spins = {}
        ore_entries = (
            ("diamond", "钻石"), ("iron", "铁"), ("gold", "金"), ("coal", "煤"),
            ("copper", "铜"), ("redstone", "红石"), ("lapis", "青金石"),
            ("emerald", "绿宝石"), ("quartz", "下界石英"), ("ancient_debris", "远古残骸"),
        )
        for index, (group_key, group_label) in enumerate(ore_entries):
            row, column = divmod(index, 5)
            check = QCheckBox(group_label)
            check.setToolTip(f"启用「自动挖掘{group_label}」模式：空闲时只识别并采集{group_label}")
            check.clicked.connect(
                lambda _enabled, key=group_key: self._set_auto_mine_modes()
            )
            spin = QSpinBox()
            spin.setRange(4, 64)
            spin.setSuffix(" 格")
            spin.setToolTip(f"「{group_label}」的识别距离：Saya 只会自动去挖这个范围内识别到的{group_label}")
            spin.editingFinished.connect(
                lambda key=group_key: self._set_auto_mine_modes()
            )
            self.auto_mine_group_checks[group_key] = check
            self.auto_mine_range_spins[group_key] = spin
            ore_grid.addWidget(check, row, column * 2)
            ore_grid.addWidget(spin, row, column * 2 + 1)
        switch_rows.addLayout(ore_grid)
        layout.addWidget(switches)

        skin_box = QGroupBox("人物皮肤（服务端皮肤 mod，默认 FabricTailor / Fabric 1.21.x）")
        skin_layout = QVBoxLayout(skin_box)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设"))
        self.skin_preset = QComboBox()
        self.skin_preset.addItem("URL（FabricTailor）", "/skin set URL {model} {value}")
        self.skin_preset.addItem("服务器文件（FabricTailor upload）", "/skin set upload {model} {value}")
        self.skin_preset.addItem("玩家名（FabricTailor）", "/skin set {model} {value}")
        self.skin_preset.addItem("SkinsRestorer", "/skin set {value}")
        self.skin_preset.currentIndexChanged.connect(self._on_skin_preset_changed)
        preset_row.addWidget(self.skin_preset)
        preset_row.addWidget(QLabel("模型"))
        self.skin_model_combo = QComboBox()
        self.skin_model_combo.addItem("经典 classic", "classic")
        self.skin_model_combo.addItem("纤细 slim", "slim")
        preset_row.addWidget(self.skin_model_combo)
        preset_row.addStretch(1)
        skin_layout.addLayout(preset_row)
        skin_row = QHBoxLayout()
        skin_row.addWidget(QLabel("皮肤值"))
        self.skin_value_input = QLineEdit()
        self.skin_value_input.setPlaceholderText("皮肤URL / 服务器文件路径 / 玩家名（留空=恢复默认）")
        self.skin_value_input.setMinimumWidth(200)
        skin_row.addWidget(self.skin_value_input, 1)
        btn_skin_apply = QPushButton("应用")
        btn_skin_apply.clicked.connect(self._apply_skin)
        skin_row.addWidget(btn_skin_apply)
        skin_layout.addLayout(skin_row)
        command_row = QHBoxLayout()
        command_row.addWidget(QLabel("命令模板"))
        self.skin_command_input = QLineEdit()
        self.skin_command_input.setPlaceholderText("/skin set URL {model} {value}（FabricTailor 默认）")
        self.skin_command_input.setMinimumWidth(200)
        command_row.addWidget(self.skin_command_input, 1)
        btn_skin_file = QPushButton("选择本地皮肤PNG…")
        btn_skin_file.clicked.connect(self._pick_skin_file)
        command_row.addWidget(btn_skin_file)
        skin_layout.addLayout(command_row)
        self.skin_hint = QLabel(
            "Fabric 1.21.5 推荐 FabricTailor（服务端 mod，1.21.4–1.21.8 均支持）：\n"
            "URL 模板 → 填皮肤图片地址；服务器文件模板 → 配置 skin_server_dir 后，本地 PNG 会自动镜像过去。\n"
            "留空皮肤值点应用 = 恢复默认。模板必须包含 {value}；{model} 会替换为 classic/slim。"
        )
        self.skin_hint.setWordWrap(True)
        skin_layout.addWidget(self.skin_hint)
        # Non-daily maintenance panels live folded inside one toolbox so the
        # live-status column stays compact (Grok-style decluttered layout).
        self.more_box = QToolBox()
        self.more_box.addItem(skin_box, "人物皮肤")

        task_box = QGroupBox("暂时当前的任务 / 实时进度")
        task_layout = QVBoxLayout(task_box)
        self.task_title = QLabel("当前空闲")
        self.task_title.setWordWrap(True)
        self.task_phase = QLabel("等待新任务")
        self.task_phase.setWordWrap(True)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        self.task_progress.setFormat("空闲")
        task_layout.addWidget(self.task_title)
        task_layout.addWidget(self.task_phase)
        task_layout.addWidget(self.task_progress)
        task_actions = QHBoxLayout()
        self.cancel_task_button = QPushButton("取消当前任务")
        self.cancel_task_button.setEnabled(False)
        self.cancel_task_button.setToolTip("取消当前计划或直接任务；不会关闭持续跟随")
        self.cancel_task_button.clicked.connect(self._cancel_current_task)
        task_actions.addWidget(self.cancel_task_button)
        self.suspend_task_button = QPushButton("挂起当前任务")
        self.suspend_task_button.setEnabled(False)
        self.suspend_task_button.setToolTip("暂停当前计划，稍后自动或手动恢复；不会丢弃进度")
        self.suspend_task_button.clicked.connect(self._suspend_current_task)
        task_actions.addWidget(self.suspend_task_button)
        task_actions.addStretch(1)
        task_layout.addLayout(task_actions)
        layout.addWidget(task_box)

        suspended_box = QGroupBox("挂起的大任务（会自动定期重试，直到你放弃）")
        suspended_layout = QVBoxLayout(suspended_box)
        self.suspended_list = QVBoxLayout()
        suspended_label = QLabel("无挂起任务")
        suspended_label.setObjectName("suspendedEmpty")
        self.suspended_list.addWidget(suspended_label)
        suspended_layout.addLayout(self.suspended_list)
        suspended_layout.addStretch(1)
        self.more_box.addItem(suspended_box, "挂起的大任务")
        layout.addWidget(self.more_box)

        self.state_labels = {}
        fields = [
            ("conn", "运行 / 连接"),
            ("lifecycle", "生命周期"),
            ("death", "死亡 / 重生"),
            ("pos", "位置"),
            ("vitals", "生命 / 饥饿 / 氧气"),
            ("survival", "生存模式"),
            ("activity", "当前任务"),
            ("follow", "跟随"),
            ("tasks", "小任务队列"),
            ("automation", "自动行为"),
            ("plan", "当前计划"),
            ("conversation", "对话路由"),
            ("players", "附近玩家"),
        ]
        for key, title in fields:
            layout.addWidget(QLabel(f"<b>{title}</b>"))
            label = QLabel("—")
            label.setWordWrap(True)
            self.state_labels[key] = label
            layout.addWidget(label)
        layout.addStretch(1)
        return box

    def _build_control_panel(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        self.toolbox = QToolBox()
        self.toolbox.addItem(self._build_common_page(), "常用（含移动）")
        self.toolbox.addItem(self._build_collect_page(), "采集")
        self.toolbox.addItem(self._build_craft_page(), "制作")
        self.toolbox.addItem(self._build_inventory_page(), "背包")
        self.toolbox.addItem(self._build_combat_page(), "战斗")
        self.toolbox.addItem(self._build_advanced_page(), "高级")
        layout.addWidget(self.toolbox, 1)
        return box

    # ---- toolbox pages ----------------------------------------------------
    def _build_common_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        grid = self._button_grid(page, [
            ("过来 come", "come"), ("跟随 follow", "follow Doctor"),
            ("停止跟随 unfollow", "unfollow"), ("停下 stop", "stop"),
            ("状态 status", "status"), ("吃东西 eat", "eat"),
            ("睡觉 sleep", "sleep"), ("查看背包 inventory", "inventory"),
        ])
        layout.addLayout(grid)
        extra = QHBoxLayout()
        btn_inv = QPushButton("查看背包 inventory")
        btn_inv.clicked.connect(lambda: self._send_direct("inventory"))
        extra.addWidget(btn_inv)
        layout.addLayout(extra)
        # Movement merged here (was its own tab): coordinate travel + jumps.
        coord_box = QGroupBox("前往坐标 goto x y z")
        coord = QHBoxLayout(coord_box)
        self.goto_x = QSpinBox()
        self.goto_x.setRange(-30000000, 30000000)
        self.goto_y = QSpinBox()
        self.goto_y.setRange(-64, 320)
        self.goto_y.setValue(64)
        self.goto_z = QSpinBox()
        self.goto_z.setRange(-30000000, 30000000)
        btn_goto = QPushButton("前往")
        btn_goto.clicked.connect(self._send_goto)
        for widget in (QLabel("X"), self.goto_x, QLabel("Y"), self.goto_y,
                       QLabel("Z"), self.goto_z, btn_goto):
            coord.addWidget(widget)
        layout.addWidget(coord_box)
        layout.addLayout(self._button_grid(page, [("跳 jump", "jump"), ("上浮 swim", "swim up")]))
        layout.addStretch(1)
        return page

    def _build_collect_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        collect_box = QGroupBox("收集方块 collect block count")
        row = QHBoxLayout(collect_box)
        self.collect_block = QComboBox()
        for label, name in COLLECT_BLOCKS:
            self.collect_block.addItem(label, name)
        self.collect_count = QSpinBox()
        self.collect_count.setRange(1, 32)
        self.collect_count.setValue(8)
        btn = QPushButton("开始采集")
        btn.clicked.connect(self._send_collect)
        row.addWidget(self.collect_block, 1)
        row.addWidget(QLabel("数量"))
        row.addWidget(self.collect_count)
        row.addWidget(btn)
        layout.addWidget(collect_box)

        chop_box = QGroupBox("砍树 chop")
        chop = QHBoxLayout(chop_box)
        self.chop_count = QSpinBox()
        self.chop_count.setRange(1, 16)
        self.chop_count.setValue(4)
        btn_chop = QPushButton("砍树")
        btn_chop.clicked.connect(
            lambda: self._send_direct(f"chop {self.chop_count.value()}")
        )
        chop.addWidget(QLabel("数量"))
        chop.addWidget(self.chop_count)
        chop.addWidget(btn_chop)
        chop.addStretch(1)
        layout.addWidget(chop_box)

        mine_box = QGroupBox("挖掘")
        mine = QHBoxLayout(mine_box)
        btn_below = QPushButton("挖脚下 mine below")
        btn_below.clicked.connect(lambda: self._send_direct("mine below"))
        btn_pickup = QPushButton("捡掉落物 pickup")
        btn_pickup.clicked.connect(lambda: self._send_direct("pickup nearest"))
        mine.addWidget(btn_below)
        mine.addWidget(btn_pickup)
        mine.addStretch(1)
        layout.addWidget(mine_box)

        warn = QLabel("mine nearby 为实验性能力，目标识别尚不稳定。")
        warn.setStyleSheet("color:#d9a441;")
        btn_nearby = QPushButton("挖附近方块 mine nearby（实验性）")
        btn_nearby.clicked.connect(lambda: self._send_direct("mine nearby"))
        layout.addWidget(btn_nearby)
        layout.addWidget(warn)
        layout.addStretch(1)
        return page

    def _build_craft_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("制作 craft item count")
        row = QHBoxLayout(box)
        self.craft_item = QLineEdit()
        self.craft_item.setPlaceholderText("搜索物品：木剑 / wooden_sword / bread …")
        self.craft_count = QSpinBox()
        self.craft_count.setRange(1, 64)
        btn = QPushButton("制作")
        btn.clicked.connect(self._send_craft)
        row.addWidget(self.craft_item, 1)
        row.addWidget(QLabel("数量"))
        row.addWidget(self.craft_count)
        row.addWidget(btn)
        layout.addWidget(box)
        self.craft_hint = QLabel("物品目录加载中…")
        self.craft_hint.setWordWrap(True)
        layout.addWidget(self.craft_hint)
        layout.addStretch(1)
        return page

    def _build_inventory_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QHBoxLayout()
        btn_refresh = QPushButton("刷新背包")
        btn_refresh.clicked.connect(self._refresh_inventory)
        self.inventory_summary = QLabel("—")
        header.addWidget(btn_refresh)
        header.addWidget(self.inventory_summary, 1)
        layout.addLayout(header)
        self.inventory_table = QTableWidget(0, 4)
        self.inventory_table.setHorizontalHeaderLabels(["物品", "数量", "类别", "操作"])
        self.inventory_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.inventory_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.inventory_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.inventory_table, 1)
        note = QLabel("每行可：丢1个 / 丢一组 / 给Doctor / 装备。规划中的材料会被后端保护。")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _build_combat_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QHBoxLayout()
        btn_refresh = QPushButton("刷新附近实体")
        btn_refresh.clicked.connect(self._refresh_entities)
        self.entity_hint = QLabel("—")
        header.addWidget(btn_refresh)
        header.addWidget(self.entity_hint, 1)
        layout.addLayout(header)
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("识别/追击范围"))
        self.combat_range = QSpinBox()
        self.combat_range.setRange(8, 128)
        self.combat_range.setValue(48)
        self.combat_range.setSuffix(" 格")
        btn_range = QPushButton("应用范围")
        btn_range.clicked.connect(
            lambda: self._set_config_number("combat_detection_range", self.combat_range.value())
        )
        range_row.addWidget(self.combat_range)
        range_row.addWidget(btn_range)
        range_row.addStretch(1)
        layout.addLayout(range_row)
        self.entity_table = QTableWidget(0, 4)
        self.entity_table.setHorizontalHeaderLabels(["实体", "类型", "距离", "操作"])
        self.entity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.entity_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.entity_table, 1)
        layout.addWidget(QLabel("敌对策略由后端配置（survival.mob_policy：counter / flee / report）。"))
        return page

    def _build_advanced_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        raw_box = QGroupBox("原始命令（doctor 来源，可用 /raw 斜杠命令）")
        raw = QHBoxLayout(raw_box)
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("例如 follow Kurosawa_Jin / place crafting_table / /say hi")
        self.cmd_input.returnPressed.connect(self._send_direct_input)
        btn_cmd = QPushButton("发送")
        btn_cmd.clicked.connect(self._send_direct_input)
        raw.addWidget(self.cmd_input, 1)
        raw.addWidget(btn_cmd)
        layout.addWidget(raw_box)

        plan_box = QGroupBox("计划控制")
        plan = QHBoxLayout(plan_box)
        btn_plan_state = QPushButton("刷新计划状态")
        btn_plan_state.clicked.connect(self._refresh_plan)
        btn_plan_cancel = QPushButton("取消当前计划")
        btn_plan_cancel.clicked.connect(self._cancel_plan)
        plan.addWidget(btn_plan_state)
        plan.addWidget(btn_plan_cancel)
        plan.addStretch(1)
        layout.addWidget(plan_box)

        bot_box = QGroupBox("机器人")
        bot = QHBoxLayout(bot_box)
        btn_start = QPushButton("启动")
        btn_start.clicked.connect(lambda: self._api("POST", f"{API}/bot/start", {}, self._on_bot_toggled))
        btn_stop = QPushButton("停止")
        btn_stop.clicked.connect(lambda: self._api("POST", f"{API}/bot/stop", {}, self._on_bot_toggled))
        bot.addWidget(btn_start)
        bot.addWidget(btn_stop)
        bot.addStretch(1)
        layout.addWidget(bot_box)

        self.debug_view = QPlainTextEdit(readOnly=True, maximumHeight=200)
        self.debug_view.setPlaceholderText("调试状态转储（高级页刷新计划时更新）")
        layout.addWidget(self.debug_view)
        return page

    @staticmethod
    def _button_grid(parent, buttons):
        from PySide6.QtWidgets import QGridLayout

        grid = QGridLayout()
        for index, (title, command) in enumerate(buttons):
            btn = QPushButton(title)
            btn.clicked.connect(lambda _=False, c=command: parent.window()._send_direct(c))
            grid.addWidget(btn, index // 2, index % 2)
        return grid

    # ------------------------------------------------------------- helpers
    def _reapply_base(self):
        self.base = self.base_input.text().strip().rstrip("/") or DEFAULT_BASE
        self._open_ws(force=True)
        self._refresh_status()
        self._load_history()
        self._refresh_sessions()
        self._load_catalog()

    def _api(self, method, path, payload, on_done):
        request = QNetworkRequest(QUrl(self.base + path))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        method = method.upper()
        if method == "GET":
            reply = self._nam.get(request)
        elif method == "POST":
            reply = self._nam.post(request, body or b"")
        elif method == "PUT":
            reply = self._nam.put(request, body or b"")
        else:
            raise ValueError(f"unsupported HTTP method: {method}")
        reply.finished.connect(lambda: self._finish_reply(reply, on_done))

    @staticmethod
    def _finish_reply(reply, on_done):
        try:
            data = bytes(reply.readAll()).decode("utf-8", "replace")
            if reply.error() != QNetworkReply.NoError:
                on_done(False, {"error": f"HTTP {reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)}: {data[:200]}"})
                return
            on_done(True, json.loads(data) if data else {})
        except json.JSONDecodeError:
            on_done(False, {"error": f"invalid JSON: {data[:200]}"})
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------ websocket
    def _open_ws(self, force=False):
        if QWebSocket is None:
            self.ws_label.setText("WebSocket：PySide6 QtWebSockets 不可用")
            return
        if self._ws is not None and self._ws.isValid() and not force:
            return
        if self._ws is None:
            self._ws = QWebSocket()
            self._ws.textMessageReceived.connect(self._on_ws_message)
            self._ws.connected.connect(lambda: self.ws_label.setText("WebSocket：已连接"))
            self._ws.disconnected.connect(lambda: self.ws_label.setText("WebSocket：已断开"))
        url = self.base.replace("http://", "ws://").replace("https://", "wss://") + API + "/ws"
        self._ws.open(QUrl(url))

    def _on_ws_message(self, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        state = data.get("state") if data.get("type") == "state" else None
        if state:
            self._render_state(state)

    # ---------------------------------------------------------------- data
    def _load_catalog(self):
        self._api("GET", f"{API}/catalog", None, self._on_catalog)
        self._api("GET", f"{API}/items", None, self._on_items)

    def _on_catalog(self, ok, data):
        if not ok:
            self.craft_hint.setText(f"能力目录加载失败：{data.get('error', '')[:100]}")
            return
        self._catalog = data
        commands = data.get("commands") or []
        self.craft_hint.setText(
            "能力目录已加载："
            + "，".join(
                f"{entry['name']}{'(实验)' if entry.get('experimental') else ''}"
                for entry in commands
                if entry.get("supports_llm")
            )[:400]
        )

    def _on_items(self, ok, data):
        if not ok:
            return
        self._items = data.get("items") or []
        names = [
            (f"{item.get('zh') or item.get('display_name')} ({item['name']})", item["name"])
            for item in self._items
        ]
        completer = QCompleter([label for label, _ in names])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.craft_item.setCompleter(completer)
        self._item_labels = dict(names)

    def _refresh_status(self):
        self._api("GET", f"{API}/bot", None, self._on_status)

    def _on_status(self, ok, data):
        if not ok:
            self.state_labels["conn"].setText(f"后端不可达：{data.get('error', '')[:120]}")
            return
        self._render_state(data)
        self._refresh_inventory()
        self._refresh_entities()

    @staticmethod
    def _task_state_badge(entry):
        state = str(entry.get("state") or "pending")
        return {
            "pending": "待完成",
            "running": "进行中",
            "retrying": "失败重试",
            "completed": "已完成",
            "failed": "已失败",
            "cancelled": "已取消",
        }.get(state, state)

    @classmethod
    def _task_queue_text(cls, auto_discard):
        if not auto_discard.get("enabled"):
            return "自动交还已关闭"
        lines = []
        current = auto_discard.get("current")
        if current:
            lines.append(f"[进行中] {current.get('label')}"
                         + (f"（第 {current.get('attempts', 0) + 1} 次尝试）" if current.get("attempts") else ""))
        pending = auto_discard.get("pending") or []
        for entry in pending[:6]:
            badge = cls._task_state_badge(entry)
            line = f"[{badge}] {entry.get('label')}"
            if entry.get("last_error"):
                line += f"（{entry['last_error']}）"
            lines.append(line)
        if not lines:
            return "队列空闲，没有等待的小任务"
        history = auto_discard.get("history") or []
        done = [h for h in history if h.get("state") in ("completed", "failed", "cancelled")]
        if done:
            latest = done[-1]
            note = latest.get("note") or latest.get("last_error") or ""
            lines.append(
                f"[{cls._task_state_badge(latest)}] {latest.get('label')}"
                + (f"（{note}）" if note else "")
            )
        return "\n".join(lines)

    def _render_state(self, state):
        self._last_state = state
        session_id = state.get("game_session_id") or ""
        if session_id and session_id != getattr(self, "_game_session_id", None):
            # Worker (re)joined a world: follow its conversation file from
            # here on so console and in-game chat stay one conversation.
            self._game_session_id = session_id
            self._load_history()
            self._refresh_sessions()
        lifecycle = state.get("lifecycle") or {}
        survival = state.get("survival") or {}
        runtime = state.get("runtime") or {}
        activity = state.get("activity") or {}
        conversation = state.get("conversation") or {}
        position = runtime.get("position") or {}
        vitals = runtime.get("vitals") or {}
        labels = self.state_labels

        labels["conn"].setText(
            f"running={bool(state.get('running'))}  connected={bool(state.get('connected'))}"
            f"  user={state.get('username') or '—'}"
        )
        fatal = lifecycle.get("fatal_reason") or ""
        labels["lifecycle"].setText(
            f"stage={lifecycle.get('stage') or '—'}  last_event={lifecycle.get('last_event') or '—'}"
            f"  reconnect={lifecycle.get('reconnect_attempts', 0)}" + (f"  FATAL={fatal}" if fatal else "")
        )
        death = lifecycle.get("death") or {}
        respawn = lifecycle.get("respawn") or {}
        kick = lifecycle.get("kick") or {}
        parts = []
        if death:
            parts.append(f"死亡@{death.get('at')}（{death.get('cause') or 'unknown'}）")
        if respawn:
            parts.append(f"重生@{respawn.get('at')}")
        if kick:
            parts.append(f"被踢：{str(kick.get('reason') or '')[:80]}")
        labels["death"].setText("；".join(parts) or "无")

        if position:
            labels["pos"].setText(
                f"({position.get('x')}, {position.get('y')}, {position.get('z')})"
                f"  {runtime.get('dimension') or ''} {runtime.get('game_mode') or ''}"
            )
        else:
            labels["pos"].setText("—")
        held = runtime.get("held_item_detail") or {}
        durability = held.get("durability") or {}
        held_text = held.get("name") or (runtime.get("held_item") or "空手")
        if durability.get("max"):
            left = max(0, int(durability["max"] or 0) - int(durability.get("used") or 0))
            held_text += f"（耐久 {left}/{durability['max']}）"
        labels["vitals"].setText(
            f"❤ {vitals.get('health', '—')}/20   🍗 {vitals.get('food', '—')}/20   "
            f"O₂ {survival.get('oxygen_level', '—')}/20   手持：{held_text}"
        )
        mode = survival.get("mode") or "normal"
        actions = survival.get("actions") or []
        labels["survival"].setText(
            f"{mode}" + (f"（{survival.get('reason')}）" if mode != "normal" else "")
            + (
                f"  最近：{'；'.join(str(item.get('text') or '') if isinstance(item, dict) else str(item) for item in actions[-3:])}"
                if actions else ""
            )
        )
        if activity.get("state") == "running":
            labels["activity"].setText(f"{activity.get('command')} — {activity.get('message')}")
        else:
            labels["activity"].setText("空闲")
        labels["follow"].setText(
            f"开关={'开' if state.get('follow_enabled') else '关'}  "
            f"target={state.get('follow_target') or '—'}  mode={state.get('follow_mode') or 'off'}"
        )
        self.follow_switch.blockSignals(True)
        self.follow_switch.setChecked(bool(state.get("follow_enabled")))
        self.follow_switch.blockSignals(False)
        auto_discard = ((state.get("automation") or {}).get("auto_discard") or {})
        auto_enabled = bool(auto_discard.get("enabled"))
        self.auto_discard_switch.blockSignals(True)
        self.auto_discard_switch.setChecked(auto_enabled)
        self.auto_discard_switch.blockSignals(False)
        combat = ((state.get("automation") or {}).get("combat") or {})
        mining = ((state.get("automation") or {}).get("mining") or {})
        self.auto_mine_switch.blockSignals(True)
        self.auto_mine_switch.setChecked(bool(mining.get("auto_mine_ores")))
        self.auto_mine_switch.blockSignals(False)
        public_config = state.get("config") or {}
        self.slu_local_switch.blockSignals(True)
        self.slu_local_switch.setChecked(str(public_config.get("slu_backend") or "") == "local")
        self.slu_local_switch.blockSignals(False)
        ore_modes = mining.get("auto_mine_ore_modes") or {}
        checked_targets = set(mining.get("auto_mine_targets") or [])
        for group_key, check in self.auto_mine_group_checks.items():
            mode = ore_modes.get(group_key) or {}
            enabled = bool(mode.get("enabled")) or group_key in checked_targets
            try:
                distance = int(mode.get("distance", self.auto_mine_range.value()))
            except (TypeError, ValueError):
                distance = self.auto_mine_range.value()
            check.blockSignals(True)
            check.setChecked(enabled)
            check.blockSignals(False)
            spin = self.auto_mine_range_spins[group_key]
            spin.blockSignals(True)
            spin.setValue(max(4, min(64, distance)))
            spin.blockSignals(False)
        public_config = state.get("config") or {}
        self._sync_skin_display(public_config)
        configured_mine_range = public_config.get("auto_mine_range")
        if configured_mine_range is not None:
            self.auto_mine_range.blockSignals(True)
            self.auto_mine_range.setValue(max(4, min(64, int(configured_mine_range))))
            self.auto_mine_range.blockSignals(False)
        configured_range = public_config.get("combat_detection_range")
        if configured_range is not None:
            self.combat_range.blockSignals(True)
            self.combat_range.setValue(max(8, min(128, int(configured_range))))
            self.combat_range.blockSignals(False)
        combat_switches = (
            (self.auto_attack_switch, "auto_attack_hostiles"),
            (self.auto_hunt_switch, "auto_hunt_animals"),
            (self.auto_counter_switch, "auto_counterattack"),
            (self.counter_owner_switch, "counterattack_owner"),
        )
        for widget, key in combat_switches:
            widget.blockSignals(True)
            widget.setChecked(bool(combat.get(key)))
            widget.blockSignals(False)
        self.counter_owner_switch.setEnabled(bool(combat.get("auto_counterattack")))
        pending = auto_discard.get("pending") or []
        last_action = auto_discard.get("last_action") or {}
        automation_text = (
            f"自动交还非任务拾取物={'开' if auto_enabled else '关'}  "
            f"执行中={bool(auto_discard.get('busy'))}  待处理={len(pending)}"
        )
        if last_action:
            automation_text += (
                f"\n最近：{last_action.get('name')} ×{last_action.get('count')}"
                f" → {last_action.get('owner') or 'Doctor'}"
            )
        if auto_discard.get("last_error"):
            automation_text += f"\n等待/错误：{auto_discard.get('last_error')}"
        automation_text += (
            f"\n主动攻击敌对={'开' if combat.get('auto_attack_hostiles') else '关'}  "
            f"狩猎动物={'开' if combat.get('auto_hunt_animals') else '关'}  "
            f"自动反击={'开' if combat.get('auto_counterattack') else '关'}  "
            f"反击主人={'开' if combat.get('counterattack_owner') else '关'}  "
            f"自动挖矿={'开' if mining.get('auto_mine_ores') else '关'}"
        )
        if mining.get("auto_mine_ores"):
            ore_modes = mining.get("auto_mine_ore_modes") or {}
            target_labels = {
                key: label for key, label in (
                    ("diamond", "钻石"), ("iron", "铁"), ("gold", "金"), ("coal", "煤"),
                    ("copper", "铜"), ("redstone", "红石"), ("lapis", "青金石"),
                    ("emerald", "绿宝石"), ("quartz", "下界石英"), ("ancient_debris", "远古残骸"),
                )
            }
            mode_texts = sorted(
                f"{target_labels.get(key, key)}{(mode or {}).get('distance', '?')}格"
                for key, mode in ore_modes.items() if (mode or {}).get("enabled")
            )
            target_text = "、".join(mode_texts) or "未启用矿种"
            automation_text += f"（{target_text}）"
            nearest = mining.get("nearest")
            if nearest:
                age = max(0, int(time.time() - float(nearest.get("at") or 0)))
                position = nearest.get("position") or [0, 0, 0]
                automation_text += (
                    f"\n最近识别：{nearest.get('name')}（{position[0]}, {position[1]}, {position[2]}）"
                    f" 距离 {nearest.get('distance')} 格 · {age}秒前"
                )
            else:
                automation_text += "\n最近识别：识别范围内暂无已启用矿种的可采矿石"
        if combat.get("busy"):
            automation_text += f"\n战斗中：{combat.get('mode')} → {combat.get('target') or '未知目标'}"
        if combat.get("last_error"):
            automation_text += f"\n战斗等待/错误：{combat.get('last_error')}"
        labels["automation"].setText(automation_text)
        labels["tasks"].setText(self._task_queue_text(auto_discard))
        stations = runtime.get("stations") or {}
        plan_text = self._plan_text(stations)
        labels["plan"].setText(plan_text)
        last = conversation.get("last") or {}
        api = conversation.get("api") or {}
        conversation_text = (
            f"queue={conversation.get('queued', 0)} busy={bool(conversation.get('busy'))}  "
            f"model={api.get('model') or '—'}{'（未配置）' if not api.get('configured') else ''}"
        )
        if last:
            conversation_text += (
                f"\nlast: route={last.get('route')} source={last.get('source')} "
                f"latency={last.get('latency_ms')}ms commands={last.get('commands') or []}"
            )
            last_at = last.get("at")
            if last_at and last_at != self._last_conversation_at:
                self._last_conversation_at = last_at
                QTimer.singleShot(0, self._load_history)
        labels["conversation"].setText(conversation_text)
        self._render_task_overview(activity, self._last_plan)
        players = (runtime.get("players") or [])[:4]
        labels["players"].setText(
            "；".join(f"{p.get('name')}({p.get('distance')}m)" for p in players) or "—"
        )

        log_lines = []
        for entry in (state.get("log") or [])[-LOG_TAIL:]:
            stamp = time.strftime("%H:%M:%S", time.localtime(float(entry.get("ts") or 0)))
            level = entry.get("level") or "info"
            log_lines.append(f"[{stamp}] [{level}] {entry.get('text')}")
        if log_lines:
            self.debug_view.setPlainText(
                "\n".join((state.get("log") or []) and log_lines)
            )

    @staticmethod
    def _plan_text(stations):
        tables = stations.get("crafting_tables") or []
        furnaces = stations.get("furnaces") or []
        furnace_types = {}
        for station in furnaces:
            name = station.get("name") or "furnace"
            furnace_types[name] = furnace_types.get(name, 0) + 1
        furnace_text = ", ".join(
            f"{name}×{count}" for name, count in sorted(furnace_types.items())
        ) or "0"
        station_text = (
            f"工作台 {len(tables)}（最近 {tables[0]['distance'] if tables else '—'}m），"
            f"熔炉类 {furnace_text}，"
            f"酿造台 {len(stations.get('brewing_stands') or [])}，"
            f"炼药锅 {len(stations.get('cauldrons') or [])}"
        )
        return f"（详情见下方；附近：{station_text}）"

    def _refresh_inventory(self):
        runtime = (self._last_state or {}).get("runtime") or {}
        inventory = runtime.get("inventory") or []
        self.inventory_table.setRowCount(0)
        zh = {item.get("name"): (item.get("zh") or item.get("display_name") or item.get("name"))
              for item in self._items}
        for row, entry in enumerate(inventory):
            name = str(entry.get("name") or "")
            self.inventory_table.insertRow(row)
            label = f"{zh.get(name, name)} ({name})"
            potion = entry.get("potion") or {}
            if potion:
                label += f" [{potion.get('container')}:{potion.get('potion')}]"
            if entry.get("selected"):
                label = "▶ " + label
            self.inventory_table.setItem(row, 0, QTableWidgetItem(label))
            self.inventory_table.setItem(row, 1, QTableWidgetItem(str(entry.get("count") or 0)))
            category = "工具/武器" if any(name.endswith(s) for s in ("pickaxe", "axe", "shovel", "hoe", "sword")) else ""
            self.inventory_table.setItem(row, 2, QTableWidgetItem(category))
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            for title, command in (
                ("丢1", f"drop {name} 1"),
                ("丢组", f"drop {name} all"),
                ("给Doctor", f"give {name} {entry.get('count') or 1} to Doctor"),
                ("装备", f"equip {name}"),
            ):
                btn = QPushButton(title)
                btn.clicked.connect(lambda _=False, c=command: self._send_direct(c))
                actions_layout.addWidget(btn)
            self.inventory_table.setCellWidget(row, 3, actions)
        total = sum(int(entry.get("count") or 0) for entry in inventory)
        self.inventory_summary.setText(f"{len(inventory)} 组 / {total} 件")

    def _refresh_entities(self):
        runtime = (self._last_state or {}).get("runtime") or {}
        configured = ((self._last_state or {}).get("config") or {}).get(
            "combat_detection_range", self.combat_range.value()
        )
        try:
            detection_range = max(8, min(128, int(configured)))
        except (TypeError, ValueError):
            detection_range = 48
        # The list and the Worker must use exactly the same range.  Otherwise
        # the UI offers an Attack button for an entity the Worker immediately
        # rejects as being outside its recognition radius.
        entities = [
            entity for entity in (runtime.get("entities") or [])
            if float(entity.get("distance") or 0) <= detection_range
        ][:24]
        self.entity_table.setRowCount(0)
        hostiles = 0
        for row, entity in enumerate(entities):
            name = str(entity.get("name") or "entity")
            self.entity_table.insertRow(row)
            self.entity_table.setItem(row, 0, QTableWidgetItem(name))
            self.entity_table.setItem(row, 1, QTableWidgetItem(
                "敌对" if entity.get("hostile") else str(entity.get("type") or "")
            ))
            self.entity_table.setItem(row, 2, QTableWidgetItem(str(entity.get("distance") or "?")))
            if entity.get("hostile"):
                hostiles += 1
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            btn = QPushButton("攻击")
            # Bind this button to the exact row entity. Several entities can
            # share the same name, while a combat task must retain its target.
            entity_id = entity.get("entity_id")
            attack_target = f"#{int(entity_id)}" if entity_id is not None else name
            btn.clicked.connect(
                lambda _=False, t=attack_target: self._send_direct(f"attack {t}")
            )
            actions_layout.addWidget(btn)
            self.entity_table.setCellWidget(row, 3, actions)
        self.entity_hint.setText(
            f"{detection_range} 格内实体 {len(entities)}（敌对 {hostiles}）"
        )

    def _refresh_plan(self):
        self._api("GET", f"{API}/plan", None, self._on_plan)

    def _on_plan(self, ok, data):
        if not ok:
            self.state_labels["plan"].setText(f"计划查询失败：{data.get('error', '')[:100]}")
            return
        self._last_plan = data
        self._render_suspended(data.get("suspended") or [])
        self._render_task_overview((self._last_state or {}).get("activity") or {}, data)
        if not data.get("active"):
            history = data.get("history") or []
            last = history[-1] if history else {}
            text = "无进行中的计划"
            if last:
                text += f"；上次：{last.get('goal_description')}（{last.get('status')}）"
            self.state_labels["plan"].setText(text)
            return
        missing = data.get("missing") or {}
        waiting = data.get("waiting_for_doctor")
        text = (
            f"{data.get('goal_description')}：第 {data.get('current_step', 0) + 1}/"
            f"{data.get('total_steps', 0)} 步 — {data.get('current_step_description')}"
        )
        if missing:
            text += f"；还需：{json.dumps(missing, ensure_ascii=False)}"
        if data.get("will_deliver"):
            text += "；完成后交付"
        if waiting:
            text += f"；等待：{waiting}"
        if data.get("last_error"):
            text += f"；最近错误：{data['last_error']}"
        self.state_labels["plan"].setText(text)
        self.debug_view.setPlainText(json.dumps(data, ensure_ascii=False, indent=2)[:4000])

    def _render_suspended(self, suspended):
        # Rebuild the dynamic rows (goal + reason + resume/abandon buttons).
        while self.suspended_list.count():
            item = self.suspended_list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if not suspended:
            empty = QLabel("无挂起任务")
            self.suspended_list.addWidget(empty)
            return
        for entry in suspended[:8]:
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            info = QLabel(
                f"{entry.get('goal_description')} — {entry.get('reason') or '条件未满足'}"
                f"（已完成 {entry.get('steps_done', 0)}/{entry.get('steps_total', 0)} 步）"
            )
            info.setWordWrap(True)
            row_layout.addWidget(info)
            buttons = QHBoxLayout()
            btn_resume = QPushButton("恢复尝试")
            btn_resume.clicked.connect(
                lambda _=False, pid=entry.get("plan_id"): self._api(
                    "POST", f"{API}/plan/resume", {"plan_id": pid}, self._on_plan
                )
            )
            btn_abandon = QPushButton("取消挂起任务")
            btn_abandon.clicked.connect(
                lambda _=False, pid=entry.get("plan_id"): self._api(
                    "POST", f"{API}/plan/abandon", {"plan_id": pid}, self._on_plan
                )
            )
            buttons.addWidget(btn_resume)
            buttons.addWidget(btn_abandon)
            buttons.addStretch(1)
            row_layout.addLayout(buttons)
            self.suspended_list.addWidget(row)

    def _render_task_overview(self, activity, plan):
        cancellable = bool(plan.get("active") or activity.get("state") == "running")
        self.cancel_task_button.setEnabled(cancellable)
        if hasattr(self, "suspend_task_button"):
            self.suspend_task_button.setEnabled(bool(plan.get("active")))
        if plan.get("active"):
            current = int(plan.get("current_step") or 0)
            total = max(1, int(plan.get("total_steps") or len(plan.get("steps") or []) or 1))
            self.task_title.setText(plan.get("goal_description") or "正在执行复杂任务")
            detail = plan.get("current_step_description") or "准备下一步"
            if plan.get("last_error"):
                detail += f"；重试原因：{plan['last_error']}"
            self.task_phase.setText(f"进行中：第 {min(current + 1, total)}/{total} 步 — {detail}")
            self.task_progress.setValue(min(99, round(current / total * 100)))
            self.task_progress.setFormat(f"计划 {current}/{total} 步")
            return
        if activity.get("state") == "running":
            progress = max(0, min(99, int(activity.get("progress") or 0)))
            self.task_title.setText(activity.get("command") or "正在执行任务")
            self.task_phase.setText(
                f"进行中：{activity.get('phase') or 'working'} — {activity.get('message') or '正在处理'}"
            )
            self.task_progress.setValue(progress)
            self.task_progress.setFormat(f"{progress}%")
            return
        last = (plan.get("history") or [])[-1] if plan else {}
        if last:
            self.task_title.setText(f"当前空闲；上次：{last.get('goal_description') or '任务'}")
            self.task_phase.setText(f"上次结果：{last.get('status') or '未知'}")
        else:
            self.task_title.setText("当前空闲")
            self.task_phase.setText("等待新任务")
        self.task_progress.setValue(0)
        self.task_progress.setFormat("空闲")

    def _cancel_plan(self):
        self._api("POST", f"{API}/plan/cancel", {}, lambda ok, data: self._refresh_plan())

    def _suspend_current_task(self):
        self.suspend_task_button.setEnabled(False)
        self._api("POST", f"{API}/plan/suspend", {}, lambda ok, data: self._refresh_plan())

    def _cancel_current_task(self):
        self.cancel_task_button.setEnabled(False)
        self._api(
            "POST", f"{API}/task/cancel", {},
            lambda ok, data: (self._refresh_plan(), self._refresh_status()),
        )

    # ---------------------------------------------------------------- chat
    def _append_chat(self, speaker, text, meta=""):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {speaker}: {text}"
        if meta:
            line += f"   {meta}"
        self.chat_view.appendPlainText(line)

    def _load_history(self):
        session_id = getattr(self, "_game_session_id", "")
        # While connected, display the CURRENT game session (same file the
        # in-game chat writes to); otherwise the separate web/desktop session.
        url = f"{API}/chat/history?limit=80"
        if session_id:
            url += f"&session={session_id}"
        self._api("GET", url, None, self._on_history)

    def _on_history(self, ok, data):
        if not ok:
            self.chat_view.setPlaceholderText(f"历史加载失败：{data.get('error', '')[:120]}")
            return
        lines = []
        for item in data.get("items") or []:
            role = "Doctor" if item.get("role") == "user" else "Saya"
            when = str(item.get("time") or "")[:19]
            source = item.get("source") or ""
            lines.append(f"[{when}] {role}: {item.get('content')}" + (f"   ({source})" if source else ""))
        self.chat_view.setPlainText("\n".join(lines))
        scrollbar = self.chat_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _refresh_sessions(self):
        self._api("GET", f"{API}/chat/sessions", None, self._on_sessions)

    def _on_sessions(self, ok, data):
        self.session_list.clear()
        if not ok:
            self.session_list.addItem("（加载失败）")
            return
        sessions = data.get("sessions") or []
        if not sessions:
            self.session_list.addItem("（还没有游戏对话记录）")
            return
        for item in sessions:
            name = str(item.get("id") or "").replace("session_mc_", "").replace(".jsonl", "")
            label = f"{name} · {item.get('entries', 0)} 条"
            if item.get("current"):
                label += " · 本次"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.ItemDataRole.UserRole, item.get("id"))
            self.session_list.addItem(list_item)

    def _selected_session_ids(self):
        ids = []
        for item in self.session_list.selectedItems():
            session_id = item.data(Qt.ItemDataRole.UserRole)
            if session_id:
                ids.append(session_id)
        return ids

    def _view_selected_session(self):
        ids = self._selected_session_ids()
        if not ids:
            self._append_chat("系统", "先在列表里选中一个会话")
            return
        self._api(
            "GET",
            f"{API}/chat/history?limit=200&session={ids[0]}",
            None,
            self._on_session_history,
        )

    def _on_session_history(self, ok, data):
        if not ok:
            self._append_chat("系统", f"会话加载失败：{(data.get('error') or '')[:120]}")
            return
        lines = []
        for item in data.get("items") or []:
            role = "Doctor" if item.get("role") == "user" else "Saya"
            when = str(item.get("time") or "")[:19]
            lines.append(f"[{when}] {role}: {item.get('content')}")
        self.history_view.setPlainText(
            chr(10).join(lines) or "（该会话没有对话内容）"
        )
        self.history_view.show()
        # Viewing an old conversation: current chat shrinks to half.
        self.chat_splitter.setSizes([500, 500])
        scrollbar = self.history_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _close_history(self):
        self.history_view.hide()
        # No historical transcript on screen: the current conversation takes
        # the full chat area again.
        self.chat_splitter.setSizes([0, 1000])
        self.chat_splitter.widget(0).setMaximumHeight(0)
        self.chat_splitter.widget(1).setMaximumHeight(16777215)

    def _merge_selected_sessions(self):
        ids = self._selected_session_ids()
        if len(ids) < 2:
            self._append_chat("系统", "合并需要按住 Ctrl 选中至少两个会话")
            return
        self._api("POST", f"{API}/chat/merge", {"sessions": ids}, self._on_sessions_merged)

    def _on_sessions_merged(self, ok, data):
        if not ok:
            self._append_chat("系统", f"合并失败：{(data.get('error') or data.get('detail') or '')[:160]}")
            return
        self._append_chat(
            "系统",
            f"已合并 {len(data.get('merged_sources') or [])} 条会话到 {data.get('target')} "
            f"（共 {data.get('entries', 0)} 条记录，被合并文件移入 archived/）",
        )
        self._refresh_sessions()

    def _archive_selected_session(self):
        ids = self._selected_session_ids()
        if not ids:
            self._append_chat("系统", "先在列表里选中一个要归档的会话")
            return
        self._api(
            "POST",
            f"{API}/chat/archive",
            {"session": ids[0]},
            self._on_session_archived,
        )

    def _on_session_archived(self, ok, data):
        if not ok:
            self._append_chat("系统", f"归档失败：{(data.get('error') or data.get('detail') or '')[:160]}")
            return
        turns = data.get("turns", 0)
        self._append_chat(
            "系统",
            f"已把游戏对话归档到主记忆（{turns} 轮，会话 {data.get('session_id', '')}），"
            f"文件已移入 archived/。",
        )
        self._refresh_sessions()

    def _send_chat(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()
        self._append_chat("Doctor", text)
        # When the console is attached to a world, keep its manual messages
        # in the same per-join transcript as in-game chat.  Offline console
        # notes remain in the separate web/desktop session.
        source = "minecraft" if getattr(self, "_game_session_id", "") else "desktop"
        self._api(
            "POST",
            f"{API}/chat",
            {
                "text": text,
                "source": source,
                "speaker": "Doctor",
                "force_command": self.chat_cmd_mode.isChecked(),
            },
            self._on_chat_reply,
        )

    def _on_chat_reply(self, ok, data):
        result = data.get("result") or {}
        if not ok:
            self._append_chat("系统", f"发送失败：{(data.get('error') or '')[:160]}")
            return
        for command in result.get("commands") or []:
            self._append_chat("→ 命令", command)
        if result.get("goal"):
            self._append_chat("→ 目标", json.dumps(result["goal"], ensure_ascii=False))
        if result.get("reply"):
            meta = (
                f"[{result.get('route')} / {result.get('source')} / {result.get('latency_ms')}ms]"
                if result.get("route")
                else ""
            )
            self._append_chat("Saya", result["reply"], meta)
        if result.get("error"):
            self._append_chat("系统", f"错误：{result['error'][:160]}")
        self._load_history()

    # -------------------------------------------------------------- control
    def _send_direct(self, text):
        if not str(text or "").strip():
            return
        self._api("POST", f"{API}/command", {"text": str(text)}, self._on_direct_sent)

    def _set_follow_enabled(self, enabled):
        self._send_direct("follow Doctor" if enabled else "unfollow")

    def _set_auto_discard_enabled(self, enabled):
        self._api(
            "PUT",
            f"{API}/bot",
            {"auto_discard_unplanned": bool(enabled)},
            self._on_automation_saved,
        )

    def _set_config_toggle(self, key, enabled):
        self._api(
            "PUT",
            f"{API}/bot",
            {str(key): bool(enabled)},
            self._on_automation_saved,
        )

    def _set_auto_mine_modes(self, _changed_key=None):
        """Send the complete per-ore mode set (one checkbox/distance edited):
        every ore group with its enable flag and detection distance."""
        modes = {
            key: {
                "enabled": check.isChecked(),
                "distance": self.auto_mine_range_spins[key].value(),
            }
            for key, check in self.auto_mine_group_checks.items()
        }
        self._api("PUT", f"{API}/bot", {"auto_mine_ore_modes": modes}, self._on_automation_saved)

    def _set_auto_mine_default_range(self):
        """Default detection distance; per-ore distances override it."""
        self._set_config_number("auto_mine_range", self.auto_mine_range.value())

    def _on_skin_preset_changed(self, index):
        template = self.skin_preset.currentData()
        if template and "{value}" in template:
            self.skin_command_input.setText(template)
            self.skin_value_input.setPlaceholderText(
                "皮肤URL / 服务器文件路径 / 玩家名（留空=恢复默认）"
            )

    def _apply_skin(self):
        """Send the value; empty = revert to the account default.  A custom
        command template (must contain {value}) adapts to any skin mod."""
        value = self.skin_value_input.text().strip()
        payload = {"value": value}
        command = self.skin_command_input.text().strip()
        if command and "{value}" in command:
            payload["command"] = command
        model = self.skin_model_combo.currentData()
        if model:
            payload["model"] = model
        self._api("POST", f"{API}/bot/skin", payload, self._on_skin_applied)

    def _on_skin_applied(self, ok, data):
        message = str(data.get("message") or "") if ok else str(data.get("error") or "请求失败")[:160]
        self.skin_hint.setText(message or ("已应用" if ok else "应用失败"))
        self._refresh_status()

    def _pick_skin_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择皮肤 PNG", str(Path.home()), "PNG 皮肤 (*.png)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as handle:
                payload = handle.read()
        except OSError as exc:
            self.skin_hint.setText(f"读取皮肤文件失败：{exc}")
            return
        boundary = "----SayaSkinBoundary7f3d"
        name = Path(path).name or "skin.png"
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = QNetworkRequest(QUrl(self.base + f"{API}/bot/skin/file"))
        request.setHeader(QNetworkRequest.ContentTypeHeader, f"multipart/form-data; boundary={boundary}")
        reply = self._nam.post(request, body)
        reply.finished.connect(lambda: self._finish_reply(reply, self._on_skin_applied))

    def _sync_skin_display(self, config):
        value = str((config or {}).get("skin_value") or "")
        if value and value != self.skin_value_input.text().strip():
            self.skin_value_input.setText(value)
        command = str((config or {}).get("skin_command") or "")
        if command and "{value}" in command and command != self.skin_command_input.text().strip():
            self.skin_command_input.setText(command)
            # Pick the matching preset entry when one exists.
            for index in range(self.skin_preset.count()):
                if self.skin_preset.itemData(index) == command:
                    self.skin_preset.blockSignals(True)
                    self.skin_preset.setCurrentIndex(index)
                    self.skin_preset.blockSignals(False)
                    break
        model = str((config or {}).get("skin_model") or "")
        if model:
            model_index = self.skin_model_combo.findData(model)
            if model_index >= 0:
                self.skin_model_combo.blockSignals(True)
                self.skin_model_combo.setCurrentIndex(model_index)
                self.skin_model_combo.blockSignals(False)

    def _set_config_number(self, key, value):
        self._api(
            "PUT",
            f"{API}/bot",
            {str(key): int(value)},
            self._on_automation_saved,
        )

    def _on_automation_saved(self, ok, data):
        if not ok:
            self._append_chat("系统", f"自动丢弃状态保存失败：{(data.get('error') or '')[:160]}")
        self._refresh_status()

    def _send_direct_input(self):
        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()
        self._send_direct(text)

    def _send_goto(self):
        self._send_direct(
            f"goto {self.goto_x.value()} {self.goto_y.value()} {self.goto_z.value()}"
        )

    def _send_collect(self):
        block = self.collect_block.currentData() or "cobblestone"
        self._send_direct(f"collect {block} {self.collect_count.value()}")

    def _send_craft(self):
        text = self.craft_item.text().strip()
        if not text:
            return
        # Accept "中文名 (name)", a bare name, or a Chinese phrase.
        name = text
        if "(" in text and text.endswith(")"):
            name = text[text.rfind("(") + 1:-1].strip()
        labels = getattr(self, "_item_labels", {})
        if name in labels:
            name = labels[name]
        if not name.replace("_", "").isascii():
            name = labels.get(text, name)
        self._send_direct(f"craft {name} {self.craft_count.value()}")

    def _on_direct_sent(self, ok, data):
        if ok:
            submitted = str(data.get("text") or "")
            command = str(data.get("command") or submitted)
            shown = f"{submitted}  →  {command}" if data.get("normalized") else command
            self._append_chat("→ 直发命令", shown)
        else:
            self._append_chat("系统", f"命令发送失败：{(data.get('error') or '')[:160]}")

    def _on_bot_toggled(self, ok, data):
        if ok:
            self._refresh_status()
        else:
            self._append_chat("系统", f"机器人操作失败：{(data.get('error') or '')[:160]}")


DARK_QSS = """
/* Unified ChatGPT-dark / Grok-minimal palette (2026-09-07 refresh):
   #212121 canvas, #2b2b2b surfaces, #303030 fields, one white accent. */
QMainWindow, QWidget { background: #212121; color: #ececec; font-size: 13px; }
QGroupBox {
    background: #2b2b2b; border: 1px solid rgba(255,255,255,0.10); border-radius: 10px;
    margin-top: 10px; padding-top: 6px; font-weight: 600; color: #d4d4d4;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #a6a6a6; }
QPushButton {
    background: #303030; border: 1px solid rgba(255,255,255,0.14); border-radius: 9px;
    padding: 5px 12px; color: #ececec; font-weight: 550;
}
QPushButton:hover { background: #3a3a3a; border-color: rgba(255,255,255,0.30); color: #ffffff; }
QPushButton:pressed { background: #282828; }
QPushButton:disabled { color: #6d6d6d; background: #262626; border-color: rgba(255,255,255,0.07); }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTableWidget {
    background: #303030; color: #ececec; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px; padding: 4px 6px; selection-background-color: #4a4a4a;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: rgba(255,255,255,0.45); }
QPlainTextEdit { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }
QListWidget, QListView {
    background: #303030; border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; padding: 2px;
}
QListWidget::item { padding: 4px 6px; border-radius: 6px; }
QListWidget::item:selected { background: rgba(255,255,255,0.14); color: #ffffff; }
QListWidget::item:hover { background: rgba(255,255,255,0.07); }
QLabel { color: #c9c9c9; }
QProgressBar {
    background: #303030; border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
    text-align: center; color: #ececec; height: 16px;
}
QProgressBar::chunk { background: #ffffff; border-radius: 6px; }
QToolBox::tab {
    background: #2b2b2b; color: #a6a6a6; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px; padding: 6px; font-weight: 600;
}
QToolBox::tab:selected { background: #343434; color: #ffffff; border-color: rgba(255,255,255,0.20); }
QToolBox::tab:hover { color: #e0e0e0; }
QSplitter::handle { background: #2b2b2b; }
QCheckBox { color: #c9c9c9; spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; }
QCheckBox::indicator:unchecked { border: 1px solid rgba(255,255,255,0.25); background: #303030; }
QCheckBox::indicator:checked { border: 1px solid #ffffff; background: #ffffff; }
QHeaderView::section {
    background: #2b2b2b; color: #a6a6a6; border: none; padding: 4px;
    border-bottom: 1px solid rgba(255,255,255,0.10);
}
QTableWidget { gridline-color: #383838; }
QStatusBar { background: #212121; color: #a6a6a6; }
QComboBox QAbstractItemView {
    background: #2b2b2b; color: #ececec; border: 1px solid rgba(255,255,255,0.14);
    selection-background-color: rgba(255,255,255,0.16);
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.16); border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.26); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.16); border-radius: 5px; min-width: 30px; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    window = CompanionConsole(base)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
