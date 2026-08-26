from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OCRLanguage:
    code: str
    chinese_name: str
    native_name: str

    @property
    def label(self) -> str:
        return f"{self.chinese_name} / {self.native_name} ({self.code})"


# 首版内置：之前使用的 23 种语言 + 简体中文。
LANGUAGES: tuple[OCRLanguage, ...] = (
    OCRLanguage("eng", "英语", "English"),
    OCRLanguage("nld", "荷兰语", "Nederlands"),
    OCRLanguage("pol", "波兰语", "Polski"),
    OCRLanguage("tur", "土耳其语", "Türkçe"),
    OCRLanguage("spa", "西班牙语", "Español"),
    OCRLanguage("fra", "法语", "Français"),
    OCRLanguage("dan", "丹麦语", "Dansk"),
    OCRLanguage("lit", "立陶宛语", "Lietuvių"),
    OCRLanguage("swe", "瑞典语", "Svenska"),
    OCRLanguage("ron", "罗马尼亚语", "Română"),
    OCRLanguage("bul", "保加利亚语", "Български"),
    OCRLanguage("fin", "芬兰语", "Suomi"),
    OCRLanguage("hrv", "克罗地亚语", "Hrvatski"),
    OCRLanguage("lav", "拉脱维亚语", "Latviešu"),
    OCRLanguage("ell", "希腊语", "Ελληνικά"),
    OCRLanguage("por", "葡萄牙语", "Português"),
    OCRLanguage("est", "爱沙尼亚语", "Eesti"),
    OCRLanguage("deu", "德语", "Deutsch"),
    OCRLanguage("slv", "斯洛文尼亚语", "Slovenščina"),
    OCRLanguage("slk", "斯洛伐克语", "Slovenčina"),
    OCRLanguage("ita", "意大利语", "Italiano"),
    OCRLanguage("ces", "捷克语", "Čeština"),
    OCRLanguage("hun", "匈牙利语", "Magyar"),
    OCRLanguage("chi_sim", "简体中文", "简体中文"),
)

LANGUAGE_BY_CODE = {item.code: item for item in LANGUAGES}
EUROPEAN_23 = tuple(item.code for item in LANGUAGES if item.code != "chi_sim")
COMMON = ("eng", "chi_sim")

