from typing import Generic, TypeVar, Callable

T = TypeVar('T')
U = TypeVar('U')


class Result(Generic[T]):
    def __init__(self, value: T | Exception):
        self.value = value

    def map(self, f: Callable[[T], U]) -> "Result[U | Exception]":
        if self.is_error():
            return self
        try:
            return Result(f(self.value))
        except Exception as e:
            return Result(e)

    def is_error(self) -> bool:
        return isinstance(self.value, Exception)

    def __await__(self):
        if self.is_error(): #Fix this
            return self
        return self.value.__await__()
