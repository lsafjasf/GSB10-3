from __future__ import annotations

from dataclasses import dataclass
from struct import unpack
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Event:
    track: int
    sequence: int
    absolute_time: int
    delta_time: int
    kind: str
    data: Tuple[int, ...]
    raw: bytes
    delta_raw: bytes
    status: Optional[int] = None
    channel: Optional[int] = None
    meta_type: Optional[int] = None

    @property
    def source_track(self) -> int:
        return self.track

    def to_bytes(self) -> bytes:
        return self.delta_raw + self.raw


class ParseError(Exception):
    def __init__(
        self,
        reason: str,
        offset: int,
        track: Optional[int] = None,
        absolute_time: Optional[int] = None,
    ) -> None:
        self.reason = reason
        self.offset = offset
        self.track = track
        self.absolute_time = absolute_time
        location = f"track {track}, " if track is not None else ""
        message = f"{reason} at {location}offset {offset}"
        super().__init__(message)


@dataclass(frozen=True)
class Header:
    format_type: int
    track_count: int
    division: int
    extra: bytes


@dataclass(frozen=True)
class UnknownChunk:
    chunk_type: bytes
    data: bytes


@dataclass(frozen=True)
class MidiFile:
    header: Header
    tracks: Tuple[Tuple[Event, ...], ...]
    unknown_chunks: Tuple[UnknownChunk, ...]

    def merged_events(self) -> List[Event]:
        return merge_tracks(self.tracks)


_CHANNEL_DATA_LENGTH: Dict[int, int] = {}
for _status in range(0x80, 0xF0):
    _CHANNEL_DATA_LENGTH[_status] = 1 if 0xC0 <= (_status & 0xF0) <= 0xDF else 2

_SYSTEM_DATA_LENGTH = {
    0xF1: 1,
    0xF2: 2,
    0xF3: 1,
    0xF6: 0,
    0xF8: 0,
    0xF9: 0,
    0xFA: 0,
    0xFB: 0,
    0xFC: 0,
    0xFE: 0,
}


def decode_vlq(data: bytes, offset: int = 0, reason: str = "unterminated-vlq") -> Tuple[int, bytes, int]:
    start = offset
    value = 0
    while True:
        if offset >= len(data):
            raise ParseError(reason, offset)
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, data[start:offset], offset


def encode_vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("VLQ values must be non-negative")
    pieces = [value & 0x7F]
    value >>= 7
    while value:
        pieces.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(pieces))


def _read_bytes(data: bytes, offset: int, count: int, reason: str) -> Tuple[bytes, int]:
    end = offset + count
    if end > len(data):
        raise ParseError(reason, offset)
    return data[offset:end], end


def parse_track(data: bytes, track_index: int = 0) -> Tuple[Event, ...]:
    offset = 0
    absolute_time = 0
    sequence = 0
    running_status: Optional[int] = None
    events: List[Event] = []

    while offset < len(data):
        try:
            delta_time, delta_raw, offset = decode_vlq(
                data, offset, reason="unterminated-vlq-delta"
            )
        except ParseError as error:
            raise ParseError(
                error.reason,
                error.offset,
                track_index,
                absolute_time,
            ) from error
        absolute_time += delta_time

        if offset >= len(data):
            raise ParseError("eof-status", offset, track_index, absolute_time)

        first_byte = data[offset]

        if first_byte < 0x80:
            if running_status is None:
                raise ParseError(
                    "missing-running-status", offset, track_index, absolute_time
                )
            status = running_status
            data_start = offset
            event_data, offset = _read_bytes(
                data,
                offset,
                _CHANNEL_DATA_LENGTH[status],
                "eof-channel-data",
            )
            event = Event(
                track=track_index,
                sequence=sequence,
                absolute_time=absolute_time,
                delta_time=delta_time,
                kind="channel",
                status=status,
                channel=status & 0x0F,
                data=tuple(event_data),
                raw=data[data_start:offset],
                delta_raw=delta_raw,
            )
        else:
            status_position = offset
            status = data[offset]
            offset += 1

            if 0x80 <= status <= 0xEF:
                running_status = status
                event_data, offset = _read_bytes(
                    data,
                    offset,
                    _CHANNEL_DATA_LENGTH[status],
                    "eof-channel-data",
                )
                kind = "channel"
                channel: Optional[int] = status & 0x0F
                meta_type: Optional[int] = None
            elif status == 0xFF:
                running_status = None
                if offset >= len(data):
                    raise ParseError("eof-meta-type", offset, track_index, absolute_time)
                meta_type = data[offset]
                offset += 1
                length, _, offset = decode_vlq(
                    data, offset, reason="unterminated-vlq-meta-length"
                )
                payload, offset = _read_bytes(
                    data, offset, length, "eof-meta-data"
                )
                kind = "meta"
                channel = None
                event_data = payload
            elif status in (0xF0, 0xF7):
                running_status = None
                length, _, offset = decode_vlq(
                    data, offset, reason="unterminated-vlq-sysex-length"
                )
                payload, offset = _read_bytes(
                    data, offset, length, "eof-sysex-data"
                )
                kind = "sysex"
                channel = None
                meta_type = None
                event_data = payload
            else:
                running_status = None
                length = _SYSTEM_DATA_LENGTH.get(status, 0)
                event_data, offset = _read_bytes(
                    data, offset, length, "eof-system-data"
                )
                kind = "system" if status in _SYSTEM_DATA_LENGTH else "unknown-system"
                channel = None
                meta_type = None

            event = Event(
                track=track_index,
                sequence=sequence,
                absolute_time=absolute_time,
                delta_time=delta_time,
                kind=kind,
                status=status,
                channel=channel,
                meta_type=meta_type,
                data=tuple(event_data),
                raw=data[status_position:offset],
                delta_raw=delta_raw,
            )

        events.append(event)
        sequence += 1

    return tuple(events)


def _chunk_header(data: bytes, offset: int, reason: str) -> Tuple[bytes, int, int]:
    if offset + 8 > len(data):
        raise ParseError(reason, offset)
    chunk_type, length = unpack(">4sI", data[offset : offset + 8])
    return chunk_type, length, offset + 8


def parse_midi_file(data: bytes) -> MidiFile:
    chunk_type, header_length, offset = _chunk_header(
        data, 0, "eof-header-chunk"
    )
    if chunk_type != b"MThd":
        raise ParseError("bad-header-id", 0)
    if header_length < 6:
        raise ParseError("bad-header-length", 8)
    if offset + header_length > len(data):
        raise ParseError("eof-header-data", offset)
    format_type, track_count, division = unpack(
        ">HHH", data[offset : offset + 6]
    )
    extra = data[offset + 6 : offset + header_length]
    offset += header_length

    tracks: List[Tuple[Event, ...]] = []
    unknown_chunks: List[UnknownChunk] = []

    while len(tracks) < track_count:
        chunk_type, length, payload_start = _chunk_header(
            data, offset, "eof-chunk-header"
        )
        payload_end = payload_start + length
        if payload_end > len(data):
            raise ParseError("eof-chunk-data", payload_start, len(tracks))
        payload = data[payload_start:payload_end]
        offset = payload_end

        if chunk_type == b"MTrk":
            tracks.append(parse_track(payload, len(tracks)))
        else:
            unknown_chunks.append(UnknownChunk(chunk_type, payload))

    return MidiFile(
        header=Header(format_type, track_count, division, extra),
        tracks=tuple(tracks),
        unknown_chunks=tuple(unknown_chunks),
    )


def parse_tracks(track_data: Sequence[bytes]) -> Tuple[Tuple[Event, ...], ...]:
    return tuple(parse_track(data, index) for index, data in enumerate(track_data))


def is_end_of_track(event: Event) -> bool:
    return event.kind == "meta" and event.meta_type == 0x2F


def merge_tracks(tracks: Iterable[Iterable[Event]]) -> List[Event]:
    merged: List[Event] = []
    for track_events in tracks:
        merged.extend(track_events)
    return sorted(
        merged,
        key=lambda event: (
            event.absolute_time,
            1 if is_end_of_track(event) else 0,
            event.track,
            event.sequence,
        ),
    )


def serialize_track(events: Iterable[Event]) -> bytes:
    return b"".join(event.to_bytes() for event in events)


def canonical_event(event: Event) -> Tuple:
    return (
        event.track,
        event.sequence,
        event.absolute_time,
        event.delta_time,
        event.kind,
        event.status,
        event.channel,
        event.meta_type,
        event.data,
        bytes(event.raw),
        bytes(event.delta_raw),
    )
