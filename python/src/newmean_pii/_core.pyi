from typing import Dict, List, Optional, Tuple, Union

VERSION: str
MODEL_VERSION: str
DEFAULT_SIZE: str
LABELS: Tuple[str, ...]
LABELS_ALL: int
SOURCE_MODEL: int
SOURCE_REGEX: int
SOURCE_TABLE: int
SOURCE_BLOCK: int

class Lists:
    def counts(self) -> Tuple[int, int]: ...

class Model:
    def __init__(self, onnx: bytes, tokenizer: bytes) -> None: ...
    def detect(
        self,
        text: str,
        sensitivity: float,
        labels: int,
        extra: Optional[List[Tuple[int, int, int]]] = ...,
        keep_emoji: bool = ...,
        whole_words: bool = ...,
        strict_short_names: bool = ...,
        lists: Optional[Lists] = ...,
    ) -> List[Tuple[int, int, int, int]]: ...
    def detect_table(
        self,
        text: str,
        sensitivity: float,
        labels: int,
        extra: Optional[List[Tuple[int, int, int]]] = ...,
        delimiter: int = ...,
        header: bool = ...,
        vote: bool = ...,
        context: int = ...,
        keep_emoji: bool = ...,
        whole_words: bool = ...,
        strict_short_names: bool = ...,
        lists: Optional[Lists] = ...,
    ) -> List[Tuple[int, int, int, int]]: ...
    def tokenize(self, text: str) -> List[Tuple[int, int, int]]: ...
    def info(self) -> Dict[str, Union[int, str]]: ...

def version() -> str: ...
def build_info() -> str: ...
def model_file(size: str, file: str) -> Optional[Tuple[str, int, str]]: ...
def default_variant(size: str) -> Optional[str]: ...
def lists_create(
    allow: List[Tuple[bytes, int]], block: List[Tuple[bytes, int]]
) -> Union[Lists, Tuple[str, int, str]]: ...
