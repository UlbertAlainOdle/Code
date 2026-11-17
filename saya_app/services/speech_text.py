"""Conservative text-only preparation shared by native and accelerated speech."""
import html
import re


EMOJI = re.compile(
    r"[0-9#*][\ufe0e\ufe0f]?\u20e3|"
    r"[\U0001f000-\U0001faff\u2600-\u27bf\u2194-\u2199\u21a9\u21aa"
    r"\u231a\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa\u24c2"
    r"\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2934\u2935\u2b05-\u2b07"
    r"\u2b1b\u2b1c\u2b50\u2b55\u3030\u303d\u3297\u3299\u00a9\u00ae\u2122]+"
)
NAMED_FACE = re.compile(
    r"\[(?:微笑|笑哭|捂脸|偷笑|大笑|害羞|可爱|调皮|眨眼|开心|难过|流泪|大哭|哭泣|委屈|"
    r"发怒|生气|惊讶|震惊|疑问|思考|叹气|无语|白眼|坏笑|比心|爱心|心碎|拥抱|抱抱|亲亲|"
    r"飞吻|点赞|鼓掌|加油|握手|玫瑰|太阳|月亮|星星|狗头|旺柴|呲牙|嘻嘻|哈哈|汗|囧|笑)\]|"
    r":(?:smile|smiley|joy|laughing|laugh|sweat_smile|wink|blush|heart|heart_eyes|sob|cry|"
    r"thinking|thinking_face|sunglasses|grin|grinning|rofl|thumbsup|thumbsdown|clap|pray|"
    r"hugging_face|sparkles|fire|tada|warning|white_check_mark|100):", re.IGNORECASE
)


def _face(match):
    value = match.group(0)
    # Avoid dropping ordinary parenthesized prose, names, formulas and citations.
    body = value[1:-1]
    if re.search(r"[\u3400-\u9fff0-9]", body):
        return value
    if re.search(r"[A-Za-z]", body) and not re.fullmatch(r"[TQOoUwW_\-^;:><\s]+", body):
        return value
    return " " if re.search(r"[ωΩ▽ヮ⌣ᴗᵕ◍•⌔﹏╥ಥಠ눈∀∇‿益Д≧≦°＾^_;]", body) or re.fullmatch(r"[><=;:._\-\s]{2,}", body) else value


def clean_speech(text):
    text = html.unescape(str(text or ""))
    text = re.sub(r"```[\s\S]*?(?:```|$)", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[(?:MOTION|MC|MOOD_SAYA|MOOD_USER)[^\]]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|span|strong|em|b|i|a|img)\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = NAMED_FACE.sub(" ", text)
    text = re.sub(r"\([^()\n]{1,64}\)|（[^（）\n]{1,64}）", _face, text)
    text = re.sub(r"(?<![A-Za-z0-9])(?:QAQ|QwQ|T_T|Q_Q|UwU|OwO|\^[_﹏-]\^)(?![A-Za-z0-9])", " ", text)
    text = re.sub(r"(?<![A-Za-z0-9/:])(?:[:;=8][-^']?[)DdpP(\\/]|<3)(?=$|[\s，。！？!?,])", " ", text)
    text = EMOJI.sub(" ", text)
    text = re.sub(r"[\u200b-\u200f\u2066-\u2069\ufe0e\ufe0f\u20e3\U000e0020-\U000e007f]", "", text)
    text = re.sub(r"\[[^\]]*$|\[[^\]]*\]\([^)]*$", "", text)
    text = re.sub(r"[*#`]", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"[ \t]+([，。！？,.!?;；:：])", r"\1", text).strip()


def speech_language(text, configured):
    """Do not force Latin words through a Chinese language token."""
    if configured != "Chinese":
        return configured
    if not re.search(r"[A-Za-z]", text):
        return configured
    if re.search(r"[\u3400-\u9fff]", text):
        return "Auto"
    # Other writing systems are not classified as English from one Latin word.
    if re.search(r"[\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]", text):
        return "Auto"
    return "English"


def prepare_speech(text, settings):
    text = clean_speech(text)
    if not re.search(r"[^\W_]", text, flags=re.UNICODE):
        raise ValueError("没有可朗读的文字，表情和装饰符号不会朗读。")
    return text, settings.model_copy(update={"language": speech_language(text, settings.language)})
