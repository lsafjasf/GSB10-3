from midi_events import encode_vlq, merge_tracks, parse_tracks


TRACKS = [
    b"\x00\xff\x51\x03\x07\xa1\x20"
    + b"\x00\x90\x3c\x64"
    + encode_vlq(10) + b"\x80\x3c\x00"
    + b"\x00\xff\x2f\x00",
    b"\x00\xc0\x05"
    + encode_vlq(10) + b"\x90\x40\x64"
    + encode_vlq(10) + b"\x80\x40\x00"
    + b"\x00\xff\x2f\x00",
    b"\x00\xff\x7e\x02\xaa\xbb"
    + encode_vlq(10) + b"\xf0\x03\x01\x02\xf7"
    + b"\x00\xff\x2f\x00",
]


def describe(event) -> str:
    if event.kind == "channel":
        return f"channel status=0x{event.status:02X} data={event.data}"
    if event.kind == "meta":
        return f"meta type=0x{event.meta_type:02X} data={event.data}"
    if event.kind == "sysex":
        return f"sysex status=0x{event.status:02X} data={event.data}"
    return f"{event.kind} status=0x{event.status:02X} data={event.data}"


def main() -> None:
    parsed = parse_tracks(TRACKS)
    print("tie rule: absolute_time -> non-EOT before EOT -> track -> sequence")
    print("abs_time  track  seq  event")
    for event in merge_tracks(parsed):
        print(
            f"{event.absolute_time:8d}  {event.track:5d}  {event.sequence:3d}  "
            f"{describe(event)}"
        )


if __name__ == "__main__":
    main()
