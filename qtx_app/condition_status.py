"""User-visible condition and provenance text, shared by the library UI."""
from qtx_core.colorimetry import display_illuminant, illuminant_note, observer_name


def condition_status(illuminant: str, observer: int) -> tuple[str, str, bool]:
    observer_name(observer)
    name = display_illuminant(illuminant)
    note = illuminant_note(illuminant)
    text = f'{name} / {observer}° · 屏幕预览'
    if note:
        text += ' · 兼容光谱'
    tooltip = ('当前色库显示条件。屏幕色块是显示近似，不能替代仪器测量或实物比色。'
               '荧光样本的其他光源结果需要相应测量条件支持。')
    if note:
        tooltip += '\n' + note
    return text, tooltip, bool(note)
