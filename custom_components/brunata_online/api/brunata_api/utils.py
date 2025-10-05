from typing import Type, TypeVar

from aiohttp import ClientResponse
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

async def from_response(response: ClientResponse, model: Type[T], strict = True) -> T:
    response.raise_for_status()
    data = await response.json()
    return model.model_validate(data, strict=strict)