from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


class ReferenceParseError(Exception):
    def __init__(self, reason: str, offset: int) -> None:
        self.reason = reason
        self.offset = offset
        super().__init__(f"{reason} at offset {offset}")


class ByteReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def read_byte(self, reason: str) -> int:
        if self.offset >= len(self.data):
            raise ReferenceParseError(reason, self.offset)
        value = self.data[self.offset]
        self.offset += 1
        return value

    def read_many(self, count: int, reason: str) -> bytes:
        start = self.offset
        result = bytearray()
        for _ in range(count):
            if self.offset >= len(self.data):
                raise ReferenceParseError(reason, start)
            result.append(self.data[self.offset])
            self.offset += 1
        return bytes(result)

    def read_vlq(self, reason: str) -> Tuple[int, bytes]:
        encoded = bytearray()
        value = 0
        while True:
            byte = self.read_byte(reason)
            encoded.append(byte)
            value = (value << 7) | (byte & 0x7F)
            if byte < 0x80:
                return value, bytes(encoded)


def _channel_data_length(status: int) -> int:
    high_nibble = status & 0xF0
    return 1 if high_nibble in (0xC0, 0xD0) else 2


def _system_data_length(status: int) -> Optional[int]:
    if status in (0xF1, 0xF3):
        return 1
    if status == 0xF2:
        return 2
    if status in (0xF6, 0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFE):
        return 0
    return None


def parse_track_reference(data: bytes, track_index: int) -> List[Tuple]:
    reader = ByteReader(data)
    events: List[Tuple] = []
    absolute_time = 0
    running_status: Optional[int] = None

    while reader.offset < len(data):
        sequence = len(events)
        delta_time, delta_raw = reader.read_vlq("unterminated-vlq-delta")
        absolute_time += delta_time

        if reader.offset >= len(data):
            raise ReferenceParseError("eof-status", reader.offset)

        first_byte = data[reader.offset]
        if first_byte < 0x80:
            if running_status is None:
                raise ReferenceParseError("missing-running-status", reader.offset)
            status = running_status
            payload = reader.read_many(
                _channel_data_length(status), "eof-channel-data"
            )
            kind = "channel"
            channel = status & 0x0F
            meta_type = None
            raw = payload
        else:
            status_start = reader.offset
            status = reader.read_byte("eof-status")

            if 0x80 <= status <= 0xEF:
                running_status = status
                payload = reader.read_many(
                    _channel_data_length(status), "eof-channel-data"
                )
                kind = "channel"
                channel = status & 0x0F
                meta_type = None
            elif status == 0xFF:
                running_status = None
                meta_type = reader.read_byte("eof-meta-type")
                length, _ = reader.read_vlq("unterminated-vlq-meta-length")
                payload = reader.read_many(length, "eof-meta-data")
                kind = "meta"
                channel = None
            elif status in (0xF0, 0xF7):
                running_status = None
                length, _ = reader.read_vlq("unterminated-vlq-sysex-length")
                payload = reader.read_many(length, "eof-sysex-data")
                kind = "sysex"
                channel = None
                meta_type = None
            else:
                running_status = None
                system_length = _system_data_length(status)
                if system_length is None:
                    payload = b""
                    kind = "unknown-system"
                else:
                    payload = reader.read_many(system_length, "eof-system-data")
                    kind = "system"
                channel = None
                meta_type = None

            raw = data[status_start : reader.offset]

        events.append(
            (
                track_index,
                sequence,
                absolute_time,
                delta_time,
                kind,
                status,
                channel,
                meta_type,
                tuple(payload),
                raw,
                delta_raw,
            )
        )

    return events


def parse_and_merge_reference(track_data: Sequence[bytes]) -> Tuple[Tuple, ...]:
    merged: List[Tuple] = []
    for track_index, data in enumerate(track_data):
        merged.extend(parse_track_reference(data, track_index))

    def sort_key(event: Tuple) -> Tuple[int, int, int, int]:
        absolute_time = event[2]
        kind = event[4]
        meta_type = event[7]
        is_end = 1 if kind == "meta" and meta_type == 0x2F else 0
        return absolute_time, is_end, event[0], event[1]

    return tuple(sorted(merged, key=sort_key))
