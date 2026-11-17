"""Read-only component probes; configured credentials do not prove provider health."""

import asyncio
import time

import httpx


async def inspect_health(bridge, credentials, client_factory, timeout=4, login_probe=None, network_probe=None):
    from .web_research.providers import selected_search
    search_provider,search_key=selected_search(credentials)
    state = bridge.status()
    result = {"checked_at": time.time(), "saya": "ready", "consumer": "draining" if state.get("draining") else "running" if state["running"] else "paused",
              "configuration": "invalid" if state["configuration_error"] else "valid",
              "napcat": "unknown", "onebot_auth": "unknown", "qq_login": "unknown",
              "model": "configured_unverified" if credentials.get("api_key") else "not_configured",
              "search": "configured_unverified" if search_key else "not_configured","search_provider":search_provider,
              "model_source": credentials.get("api_source", "unconfigured"), "requires_action": []}
    if not bridge.settings or state["configuration_error"]:
        result["requires_action"].append("修复本地 QQ 连接配置")
        return result
    # A paused consumer has no client. A diagnostic client must never start it.
    own_client = bridge.client is None
    client = client_factory(bridge.settings) if own_client else bridge.client
    try:
        status = await asyncio.wait_for(client.call("get_status", {}), timeout)
        result.update(napcat="reachable", onebot_auth="accepted")
        if not isinstance(status, dict) or status.get("online") is not True:
            result["qq_login"] = "login_required"
            result["requires_action"].append("在 NapCat 管理页用机器人 QQ 重新扫码确认")
        else:
            info = await asyncio.wait_for(client.call("get_login_info", {}), timeout)
            matches = isinstance(info, dict) and str(info.get("user_id")) == bridge.settings.bot_id
            result["qq_login"] = "matched" if matches else "wrong_account"
            if not matches:
                result["requires_action"].append("登录账号与本地机器人配置不一致")
    except httpx.HTTPStatusError as error:
        result["napcat"] = "reachable"
        result["onebot_auth"] = "rejected" if error.response.status_code in {401, 403} else "http_error"
        result["requires_action"].append("检查 OneBot 地址和鉴权配置")
    except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError):
        result["napcat"] = "unreachable"
        result["requires_action"].append("OneBot 服务不可达，请检查 NapCat 进程、网络或端口")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result["napcat"] = "timeout"
        result["requires_action"].append("OneBot 响应超时，请检查服务和网络")
    except Exception:
        result["napcat"] = "action_failed"
        result["requires_action"].append("OneBot 未返回可用状态，请在 NapCat 管理页核对登录")
    finally:
        if own_client:
            await client.close()
    if login_probe and result['qq_login'] in {'unknown','login_required'}:
        try:
            native=await asyncio.wait_for(login_probe(),3)
            if native in {'login_required','offline','login_initializing','logged_in_unverified'}:
                result['qq_login']=native
                result['requires_action']=[text for text in result['requires_action'] if '重新扫码' not in text]
                if native=='login_required':result['requires_action'].insert(0,'在 NapCat 管理页用机器人 QQ 重新扫码确认')
                elif native=='offline':result['requires_action'].insert(0,'QQ连接离线，请先检查网络和NapCat状态')
                elif native=='login_initializing':result['requires_action'].insert(0,'已确认登录，正在等待NapCat初始化，请稍后重试')
                else:result['requires_action'].insert(0,'QQ已登录，仍需检查OneBot连接与鉴权后核对账号')
        except Exception:
            pass  # An unavailable management API cannot turn unknown into healthy.
    if network_probe is not None:
        try:
            network = await asyncio.wait_for(network_probe(), timeout=2.2)
            result['network'] = network if network in {'reachable', 'unreachable'} else 'unknown'
        except Exception:
            result['network'] = 'unknown'
        if result['network'] != 'reachable':
            result['requires_action'].append('外网连通性尚未确认；账号匹配不代表网络可用，请检查连接后重试')
    if result["model"] == "not_configured":
        result["requires_action"].append("配置服务端模型连接")
    if result["search"] == "not_configured":
        result["requires_action"].append("网络搜索尚未配置密钥")
    return result
