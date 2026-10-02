"""
清大系所名稱的正規化。

正式名稱來自 NTHU API 的單位目錄（`/departments/`），只取學術單位的名稱，不保存其中的人員資料。
使用者常說簡稱（「資工」「電機系」），先查下面的簡稱表，再做名稱比對；
簡稱表只是捷徑，指到的名稱必須真的出現在官方清單裡才算數。
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

CACHE_SECONDS = 24 * 60 * 60
ACADEMIC_UNIT = re.compile(r"(學系|系|研究所|學位學程|學士班)$")
NOISE = re.compile(r"^(我是|我讀|我念|我唸|就讀|念|唸)|(國立)?清(華|大)(大學)?|的$|\s")

# 常見簡稱 → 正式名稱（必須存在於官方清單）
ALIASES = {
    "資工": "資訊工程學系",
    "電機": "電機工程學系",
    "化工": "化學工程學系",
    "動機": "動力機械工程學系",
    "材料": "材料科學工程學系",
    "材料系": "材料科學工程學系",
    "工工": "工業工程與工程管理學系",
    "工工系": "工業工程與工程管理學系",
    "工科": "工程與系統科學系",
    "工科系": "工程與系統科學系",
    "醫環": "生醫工程與環境科學系",
    "醫環系": "生醫工程與環境科學系",
    "中文": "中國文學系",
    "中文系": "中國文學系",
    "外語": "外國語文學系",
    "外語系": "外國語文學系",
    "生科": "生命科學系",
    "生科系": "生命科學系",
    "醫科": "醫學科學系",
    "醫科系": "醫學科學系",
    "計財": "計量財務金融學系",
    "計財系": "計量財務金融學系",
    "經濟": "經濟學系",
    "經濟系": "經濟學系",
    "物理": "物理學系",
    "物理系": "物理學系",
    "數學": "數學系",
    "化學": "化學系",
    "教科": "教育與學習科技學系",
    "教科系": "教育與學習科技學系",
    "幼教": "幼兒教育學系",
    "幼教系": "幼兒教育學系",
    "特教": "特殊教育學系",
    "特教系": "特殊教育學系",
    "英教": "英語教學系",
    "英教系": "英語教學系",
    "教心": "教育心理與諮商學系",
    "教心系": "教育心理與諮商學系",
    "環文": "環境與文化資源學系",
    "環文系": "環境與文化資源學系",
    "運科": "運動科學系",
    "運科系": "運動科學系",
    "藝設": "藝術與設計學系",
    "藝設系": "藝術與設計學系",
    "音樂": "音樂學系",
    "音樂系": "音樂學系",
    "清華學院": "清華學院學士班",
    "電資院學士班": "電資院學士班",
}

Fetch = Callable[[], Awaitable[Any]]


class DepartmentDirectory:
    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._names: tuple[str, ...] = ()
        self._expires = 0.0

    async def names(self) -> tuple[str, ...]:
        """官方學術單位名稱；API 失敗時沿用上一份（可能是空的）。"""
        now = time.monotonic()
        if self._names and now < self._expires:
            return self._names
        try:
            units = await self._fetch() or []
            names = sorted(
                {
                    unit["name"].strip()
                    for unit in units
                    if isinstance(unit, dict)
                    and isinstance(unit.get("name"), str)
                    and ACADEMIC_UNIT.search(unit["name"].strip())
                }
            )
            if names:
                self._names = tuple(names)
                self._expires = now + CACHE_SECONDS
        except Exception as error:  # noqa: BLE001 -- 沒有清單時退回自由文字
            logger.warning("Department directory unavailable: {}", type(error).__name__)
        return self._names

    async def resolve(self, text: str) -> tuple[str | None, list[str]]:
        """
        使用者說的系所 → (正式名稱, 候選清單)。

        唯一確定時回傳名稱；有多個可能時回傳候選（最多 5 個）讓模型反問；
        完全對不到或清單不可用時回傳 (None, [])。
        """
        query = NOISE.sub("", text or "")
        names = await self.names()
        if not query or not names:
            return None, []
        alias = ALIASES.get(query)
        if alias in names:
            return alias, []
        if query in names:
            return query, []
        stem = re.sub(r"(學系|系|研究所|所)$", "", query)
        if ALIASES.get(stem) in names:
            return ALIASES[stem], []
        candidates = [name for name in names if stem and stem in name]
        if len(candidates) == 1:
            return candidates[0], []
        # 「資訊工程」這類去掉字尾後的完整名稱：優先選學系
        exact = [name for name in candidates if re.sub(ACADEMIC_UNIT, "", name) == stem]
        if len(exact) == 1:
            return exact[0], []
        return None, candidates[:5]
