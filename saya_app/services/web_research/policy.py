"""Trigger policy for the web research pipeline (plan sections 8 & 10).

Responsibilities:
- ``normalize_mode``: map any raw value to ``off`` / ``auto`` / ``force``,
  falling back to ``off`` for unknown values.
- ``should_research``: decide, given a normalized mode and the user message,
  whether to actually run the pipeline.
- ``extract_direct_urls``: pull explicit http(s) URLs from the message.
- ``build_query``: clean a user message into a search query (strip command
  prefixes, remove direct URLs, merge whitespace, cap length).
"""

import re
from urllib.parse import urlsplit

from saya_app.services.web_research.models import SearchPlan


VALID_MODES = ("off", "auto", "force")
VALID_SOURCE_STRATEGIES = ("official_first", "balanced", "community")

# Direct http(s) URLs. Rejects ftp/mailto/etc.
_URL_RE = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE,
)

# Command prefixes that should be stripped when building a query (plan 10.3).
_PREFIX_PATTERNS = [
    r"帮我搜索",
    r"帮我查一下",
    r"请帮我搜索",
    r"请搜索",
    r"请联网查一下",
    r"请帮我查一下",
    r"搜索一下",
    r"查一下",
    r"查查",
    r"上网查",
    r"联网查",
    r"网页搜索",
    r"搜索",
    r"please\s+search",
    r"please\s+look\s+up",
    r"search\b",
    r"look\s+up\b",
    r"browse\s+the\s+web\b",
]
_PREFIX_RE = re.compile("|".join(_PREFIX_PATTERNS), re.IGNORECASE)

# Explicit request words that signal the user wants to go online (plan 10.3).
_REQUEST_WORDS = [
    "搜索", "查一下", "查查", "上网查", "联网查", "网页搜索",
    "search", "look up", "browse the web",
]
_REQUEST_RE = re.compile("|".join(re.escape(w) for w in _REQUEST_WORDS), re.IGNORECASE)

# Time-sensitive targets that, combined with a fact/time intent, trigger auto.
_TIME_FACTORS = [
    "最新", "新闻", "实时", "价格", "汇率", "天气", "比分",
    "当前版本", "最新版本", "发布日期", "刚刚发生",
    "最新政策", "现行规定", "目前负责人",
]
_TIME_FACTORS_RE = re.compile("|".join(re.escape(w) for w in _TIME_FACTORS))

# Bare "now"/"current" must NOT trigger alone (plan 10.3 last rule).
_BARE_TIME_RE = re.compile(r"现在|当前|currently|right now", re.IGNORECASE)


def normalize_mode(raw) -> str:
    """Normalize an arbitrary input to a valid mode, defaulting to ``off``.

    Accepts strings (case-insensitive) and common Chinese labels. Unknown or
    empty values fall back to ``off`` (plan section 8).
    """
    if raw is None:
        return "off"
    text = str(raw).strip().lower()
    mapping = {
        "": "off",
        "off": "off",
        "关闭": "off",
        "关": "off",
        "false": "off",
        "0": "off",
        "auto": "auto",
        "自动": "auto",
        "force": "force",
        "强制": "force",
        "本次强制": "force",
        "on": "force",
        "打开": "force",
        "true": "force",
        "1": "force",
    }
    return mapping.get(text, "off")


def extract_direct_urls(message: str):
    """Return all explicit http(s) URLs found in ``message``, de-duplicated.

    Order is preserved. Only http/https schemes are kept; the scheme is
    validated via urlsplit so ``httpfoo`` is not matched as a URL. De-duplication
    uses the canonical form so ``https://a.com`` and ``https://a.com/`` collide.
    """
    # Local import to avoid a hard dependency cycle at import time.
    from saya_app.services.web_research.search import canonicalize_url

    if not message:
        return []
    seen = set()
    urls = []
    for match in _URL_RE.findall(message):
        # Clean trailing punctuation that often clings to pasted URLs.
        cleaned = match.rstrip(".,);:!?'\"]>")
        scheme = urlsplit(cleaned).scheme.lower()
        if scheme not in ("http", "https"):
            continue
        canon = canonicalize_url(cleaned)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        urls.append(canon)
    return urls


def has_explicit_request(message: str) -> bool:
    """True if the message contains an explicit search/browsing request word."""
    return bool(_REQUEST_RE.search(message or ""))


def has_time_factor(message: str) -> bool:
    """True if the message contains a clear time-sensitive/factual target."""
    return bool(_TIME_FACTORS_RE.search(message or ""))


def is_emotional_or_persona(message: str) -> bool:
    """Heuristic for messages that must NOT trigger auto research.

    Catches the documented false-positive cases: emotional statements and
    requests to just chat now (plan section 10.3 "不得触发").
    """
    if not message:
        return False
    lowered = message.lower()
    emotional_markers = [
        "难过", "开心", "伤心", "生气", "孤独", "害怕", "焦虑", "累",
        "陪我聊", "陪我聊天", "聊聊天", "陪我说",
    ]
    for marker in emotional_markers:
        if marker in lowered:
            return True
    return False


def should_research(mode: str, message: str) -> bool:
    """Decide whether research should run for this message.

    - ``off``: never (plan 10.1).
    - ``force``: always try (plan 10.2).
    - ``auto``: only when there is a direct URL, an explicit request word, or
      a time-sensitive factor; emotional/persona chat is excluded. A bare
      "now"/"current" never triggers alone (plan 10.3).
    """
    mode = normalize_mode(mode)
    if mode == "off":
        return False
    if mode == "force":
        return True

    # auto
    if not message:
        return False
    if extract_direct_urls(message):
        return True
    if is_emotional_or_persona(message):
        return False
    if has_explicit_request(message):
        return True
    if has_time_factor(message):
        return True
    # Bare "现在"/"当前" alone must not trigger.
    if _BARE_TIME_RE.search(message) and not (has_time_factor(message)):
        return False
    return False


def build_query(message: str, max_chars: int = 400) -> str:
    """Clean a user message into a search query (plan 10.3).

    Removes direct URLs and search command prefixes, merges whitespace, and
    caps length. The result is the query sent to Brave (no extra model call).
    """
    if not message:
        return ""
    text = message
    # Remove direct URLs entirely.
    for url in extract_direct_urls(text):
        text = text.replace(url, " ")
    # Strip a leading command prefix once.
    text = _PREFIX_RE.sub(" ", text, count=1)
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


_COMMUNITY_INTENT_RE = re.compile(
    r"体验|评价|口碑|测评|评测|用户反馈|网友|论坛|社区|个人看法|大家觉得|"
    r"风评|舆论|网民|公众看法|社会评价|怎么看|看法|态度|印象|声誉|争议|"
    r"review|reviews|experience|opinion|reputation|public opinion|community|forum|reddit|知乎",
    re.IGNORECASE,
)
_CHINA_REGION_RE = re.compile(r"中国|国内|大陆|中华人民共和国", re.IGNORECASE)
_JAPAN_REGION_RE = re.compile(r"日本|赴日|Japan|Japanese", re.IGNORECASE)
_JAPAN_STUDY_RE = re.compile(
    r"留学|留学生|学生签证|在留资格|大学|大学院|博士|入学|奖学金|学费|"
    r"study\s+in\s+japan|international\s+student|student\s+visa|graduate\s+school",
    re.IGNORECASE,
)
_GOVERNMENT_TOPIC_RE = re.compile(
    r"政府|部门|监管|法律|法规|政策|条例|办法|通知|规定|标准|税务|社保|医保|"
    r"许可|处罚|实施时间|生效时间",
    re.IGNORECASE,
)
_DIRECT_GOVERNMENT_RE = re.compile(
    r"国务院|工信部|教育部|卫健委|人民银行|证监会|国家统计局|最高人民法院|"
    r"最高人民检察院|全国人大|海关总署|市场监管总局",
    re.IGNORECASE,
)
_TECHNICAL_RE = re.compile(
    r"\b(?:api|sdk|python|javascript|typescript|java|rust|github|llm|ai|model|"
    r"version|release|docs?|documentation|context|token|framework|library)\b|"
    r"模型|上下文|参数|版本|发布说明|接口|开发文档|软件|框架|代码库|安装|配置",
    re.IGNORECASE,
)
_RESEARCH_RE = re.compile(
    r"论文|研究|临床|试验|医学|药物|疾病|指南|共识|paper|research|study|"
    r"clinical|trial|doi|arxiv|pubmed|学术|文献|引用|citation",
    re.IGNORECASE,
)
_JOURNAL_METRIC_RE = re.compile(
    r"期刊|影响因子|影响力因子|中科院分区|JCR分区|\bJCR\b|\bSCI\b|\bSSCI\b|"
    r"\bISSN\b|journal impact factor|impact factor|journal citation reports",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(r"新闻|刚刚|今天|昨日|本周|最新消息|news|breaking", re.IGNORECASE)
_OFFICIAL_WORD_RE = re.compile(
    r"官方|官网|原文|公告|文档|规范|标准|准确|权威|official|primary source|"
    r"documentation|specification|release notes",
    re.IGNORECASE,
)


def normalize_source_strategy(raw) -> str:
    value = str(raw or "").strip().lower()
    return value if value in VALID_SOURCE_STRATEGIES else "official_first"


def _append_query_terms(query: str, suffix: str, max_chars: int = 400) -> str:
    if not query or not suffix:
        return query[:max_chars]
    combined = f"{query} {suffix}".strip()
    return combined[:max_chars].rstrip()


def build_search_plan(message: str, source_strategy="official_first") -> SearchPlan:
    """Build a lightweight, deterministic source plan without another LLM call.

    Language is selected from the subject, not merely the user's writing
    language: technical/product facts prefer original English documentation,
    while Chinese law/policy facts prefer Chinese primary authorities.
    """
    query = build_query(message)
    strategy = normalize_source_strategy(source_strategy)
    community_intent = bool(_COMMUNITY_INTENT_RE.search(query))
    china_official = bool(
        _DIRECT_GOVERNMENT_RE.search(query)
        or (_CHINA_REGION_RE.search(query) and _GOVERNMENT_TOPIC_RE.search(query))
    )
    technical = bool(_TECHNICAL_RE.search(query))
    research = bool(_RESEARCH_RE.search(query))
    journal_metric = bool(_JOURNAL_METRIC_RE.search(query))
    news = bool(_NEWS_RE.search(query))

    if strategy == "community" or community_intent:
        return SearchPlan(
            query=query,
            topic_type="community",
            source_strategy="community" if strategy == "community" else "balanced",
            search_lang="zh-hans" if re.search(r"[\u4e00-\u9fff]", query) else "en",
            ui_lang="zh-CN" if re.search(r"[\u4e00-\u9fff]", query) else "en-US",
        )

    if journal_metric:
        if strategy == "official_first" and not _OFFICIAL_WORD_RE.search(query):
            query = _append_query_terms(
                query, "official Journal Impact Factor Clarivate JCR"
            )
        return SearchPlan(
            query=query,
            topic_type="journal_metric",
            source_strategy=strategy,
            search_lang="en",
            ui_lang="en-US",
            requires_primary=(strategy == "official_first"),
        )

    if china_official:
        if strategy == "official_first" and not _OFFICIAL_WORD_RE.search(query):
            query = _append_query_terms(query, "政府官网 官方 原文")
        return SearchPlan(
            query=query,
            topic_type="government",
            source_strategy=strategy,
            search_lang="zh-hans",
            country="CN",
            ui_lang="zh-CN",
            freshness="pw" if news else "",
            requires_primary=(strategy == "official_first"),
        )

    if _JAPAN_REGION_RE.search(query) and (
        _JAPAN_STUDY_RE.search(query) or _GOVERNMENT_TOPIC_RE.search(query)
    ):
        query = _append_query_terms(
            query,
            "日本 留学生 在留資格 文部科学省 出入国在留管理庁 JASSO 公式",
        )
        return SearchPlan(
            query=query,
            topic_type="government",
            source_strategy=strategy,
            search_lang="",
            country="JP",
            ui_lang="ja-JP",
            # Policies relevant one year from now need a wider window than
            # breaking news; current rules may have been announced months ago.
            freshness="py" if news else "",
            requires_primary=(strategy == "official_first"),
        )

    if technical:
        if strategy == "official_first" and not _OFFICIAL_WORD_RE.search(query):
            query = _append_query_terms(query, "official documentation")
        return SearchPlan(
            query=query,
            topic_type="technical",
            source_strategy=strategy,
            search_lang="en",
            ui_lang="en-US",
            requires_primary=(strategy == "official_first"),
        )

    if research:
        if strategy == "official_first" and not _OFFICIAL_WORD_RE.search(query):
            query = _append_query_terms(query, "primary source paper")
        return SearchPlan(
            query=query,
            topic_type="research",
            source_strategy=strategy,
            search_lang="en",
            ui_lang="en-US",
            requires_primary=(strategy == "official_first"),
        )

    return SearchPlan(
        query=query,
        topic_type="news" if news else "general",
        source_strategy=strategy,
        search_lang="",
        ui_lang="",
        freshness="pw" if news else "",
        requires_primary=False,
    )


# ============================================================
# Recall trigger policy (memory depth control)
# ============================================================

# Recall-specific trigger words: references to past conversations or requests
# that benefit from memory context.
_RECALL_WORDS = [
    "记得", "上次", "之前", "你说过", "我们聊过", "还记得", "记忆", "回忆",
    "提到过", "说过的", "讨论过", "问过", "告诉过", "讲过",
    "remind", "remember", "last time", "previously", "earlier", "you said",
    "we discussed", "we talked", "as you mentioned",
]
_RECALL_RE = re.compile("|".join(re.escape(w) for w in _RECALL_WORDS), re.IGNORECASE)


def normalize_recall_mode(raw) -> str:
    """Normalize recall mode to ``off`` / ``auto`` / ``on``.

    Defaults to ``auto`` (normal recall behavior) for unknown values.
    """
    if raw is None:
        return "auto"
    text = str(raw).strip().lower()
    mapping = {
        "": "auto",
        "off": "off",
        "关闭": "off",
        "关": "off",
        "auto": "auto",
        "自动": "auto",
        "on": "on",
        "force": "on",
        "强制": "on",
        "打开": "on",
        "deep": "on",
        "normal": "auto",
    }
    return mapping.get(text, "auto")


def has_recall_trigger(message: str) -> bool:
    """True if the message references past context or uses recall/search words."""
    if not message:
        return False
    if _RECALL_RE.search(message):
        return True
    # Explicit search request words also justify recalling relevant memories.
    if has_explicit_request(message):
        return True
    from ..memory_recall_contract import personal_recall_intent
    return personal_recall_intent(message)


def should_recall(recall_mode: str, message: str) -> bool:
    """Decide whether memory recall should run for this message.

    - ``off``: never — skip all memory context.
    - ``on``: always — use deep recall mode.
    - ``auto``: recall in normal mode only when the message references past
      context or contains explicit request/search words. Emotional language
      does not veto an explicit reference to a past experience.
    """
    mode = normalize_recall_mode(recall_mode)
    if mode == "off":
        return False
    if mode == "on":
        return True
    # auto
    if not message:
        return False
    return has_recall_trigger(message)
