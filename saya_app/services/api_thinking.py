"""Provider-scoped thinking controls; never send DeepSeek fields to other APIs."""
from urllib.parse import urlparse


def supports_thinking(api_url, model):
    host = urlparse(str(api_url)).hostname or ""
    name = str(model).lower().split("/")[-1]
    return host == "api.deepseek.com" and (name.startswith("deepseek-v4") or name == "deepseek-flash")


def apply(payload, api_url, effort):
    if not supports_thinking(api_url, payload.get("model")):
        if effort not in ("auto", "none"):
            raise ValueError("当前接口或模型未验证思考程度控制。请选择自动，或使用 DeepSeek V4 / Flash 官方接口。")
        return payload
    if effort == "none":
        payload["thinking"] = {"type": "disabled"}
    else:
        payload["thinking"] = {"type": "enabled"}
        if effort != "auto":
            payload["reasoning_effort"] = effort
    return payload
