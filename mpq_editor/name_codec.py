"""MPQ 文件名编解码。

(listfile) 中的文件名可能以不同编码存储（ASCII、GBK、UTF-8 …）。
用固定编码解码会让其它编码的名字显示为乱码；而 StormLib 以原始字节
计算名字哈希，重新编码时必须还原出完全相同的字节才能找到文件。

本类对每个名字依次尝试一组编码（严格解码），记住成功的那一个，
从而同时保证显示正确与字节往返一致。
"""

from __future__ import annotations

FALLBACK_CODECS = ("utf-8", "gbk")


class MpqNameCodec:
    """按名字记忆编码的 MPQ 文件名编解码器。"""

    def __init__(self, default_encoding: str = "utf-8"):
        self.default_encoding = default_encoding
        self._codecs: dict[str, str] = {}

    def set_encoding(self, encoding: str) -> None:
        """切换默认编码。已记录的名字保留原编码，保证往返不变。"""
        self.default_encoding = encoding

    def _chain(self) -> list[str]:
        chain = [self.default_encoding, *FALLBACK_CODECS]
        seen: set[str] = set()
        out: list[str] = []
        for codec in chain:
            if codec.lower() not in seen:
                seen.add(codec.lower())
                out.append(codec)
        return out

    def decode(self, raw: bytes) -> str:
        """把原始字节解码为显示字符串，并记住可往返的编码。"""
        for codec in self._chain():
            try:
                name = raw.decode(codec)
            except (UnicodeDecodeError, LookupError):
                continue
            self._codecs[name] = codec
            return name
        return raw.decode(self.default_encoding, errors="surrogateescape")

    def encode(self, name: str) -> bytes:
        """把显示字符串编码回原始字节（与 decode 互逆）。"""
        codec = self._codecs.get(name)
        if codec is not None:
            try:
                return name.encode(codec)
            except UnicodeEncodeError:
                pass
        return name.encode(self.default_encoding, errors="surrogateescape")
