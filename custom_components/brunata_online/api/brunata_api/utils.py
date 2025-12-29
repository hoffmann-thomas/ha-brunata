import json
from typing import Type, TypeVar

from aiohttp import ClientResponse
from pydantic import BaseModel
from yarl import URL

T = TypeVar("T", bound=BaseModel)

async def from_response(response: ClientResponse, model: Type[T], strict = True) -> T:
    response.raise_for_status()
    data = await response.json()
    return model.model_validate(data, strict=strict)



def pretty_print_aiohttp_request(method: str, url: str, **kwargs) -> str:
    """
    Pretty-print an aiohttp request before sending it.

    Example:
        async with aiohttp.ClientSession() as session:
            print(pretty_print_aiohttp_request('POST', 'https://example.com', json={'a': 1}))
            async with session.post('https://example.com', json={'a': 1}) as resp:
                ...
    """
    headers = kwargs.get("headers", {})
    data = kwargs.get("data")
    json_data = kwargs.get("json")
    params = kwargs.get("params")

    # Rebuild full URL if params are provided
    full_url = str(URL(url).update_query(params or {}))

    # Handle body
    if json_data is not None:
        body = json.dumps(json_data, indent=2)
    elif isinstance(data, (dict, list, tuple)):
        body = json.dumps(data, indent=2)
    else:
        body = data or ""

    # Build formatted string
    return (
        "-----------START-----------\n"
        f"{method.upper()} {full_url}\n"
        + "\r\n".join(f"{k}: {v}" for k, v in headers.items())
        + "\r\n\r\n"
        + (body if isinstance(body, str) else str(body))
    )