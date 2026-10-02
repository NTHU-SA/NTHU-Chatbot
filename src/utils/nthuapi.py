import os

import httpx
from cachetools import TTLCache

from log import logger

API_ENDPOINT = os.getenv("API_ENDPOINT", "https://api.nthusa.tw")
if API_ENDPOINT.endswith("/"):
    API_ENDPOINT = API_ENDPOINT[:-1]

_cache = TTLCache(maxsize=128, ttl=60 * 60)
_default_headers = {
    "Host": "api.nthusa.tw",
    "User-Agent": "NTHU Chatbot/1.0",
}


async def get(
    path: str, cache: bool = True, params: dict = None, *args, **kwargs
) -> list | dict | None:
    """
    [Async] 從 API 獲取資料並快取結果 (使用 httpx.AsyncClient)。
    Args:
        path (str): API 路徑。
        cache (bool): 是否快取結果。
        params (dict): API 請求參數 (queryString)。
    Returns:
        list | dict | None: API 回傳的資料。
    """
    if not path.startswith("/"):
        path = "/" + path

    cache_key = (
        path,
        tuple(sorted(params.items())) if params else None,
    )  # 將 path 和 params 納入快取鍵

    if cache and cache_key in _cache:
        return _cache[cache_key]

    url = API_ENDPOINT + path
    logger.info(f"Calling API: {url} with params: {params}")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers=_default_headers, timeout=10
        ) as client:
            response = await client.get(
                *args, url=url, params=params, **kwargs
            )  # 用 client 層級的預設 timeout
            response.raise_for_status()  # 確保 HTTP 請求成功 (2xx 狀態碼)
        result = response.json()
        if cache:
            _cache[cache_key] = result
        return result
    except httpx.HTTPError as e:
        logger.error(f"API request failed for URL: {url}, Error: {e}")
        raise ValueError(f"API request failed: {e}") from e
