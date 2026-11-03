"""requirements.txt, trimmed for a Ken set up without voice notes (KEN_VOICE=0).

The local transcription model (faster-whisper) and what only it needs take about 1.5 GB of memory
once loaded and a lot of disk, which a small computer in the cloud can't spare. requirements.txt
is compiled with "# via" notes, so a part can be left out when everything that wanted it is.
"""
from __future__ import annotations

import re
import sys

VOICE = {"faster-whisper", "av", "onnxruntime"}  # av decodes voice notes; onnxruntime finds speech in them
KEEP = {"numpy"}  # video_kit uses it, though only voice asks for it


def _name(line: str) -> str:
    return re.split(r"[=<>!~ ;\[]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")


def without_voice(text: str, drop: set[str] = VOICE) -> str:
    """The same pins without `drop` and every part that only they needed."""
    blocks: list[tuple[str, list[str], set[str]]] = []  # (name, lines, wanted by)
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            blocks.append((_name(line), [line], set()))
        elif blocks:
            blocks[-1][1].append(line)
            via = line.strip().removeprefix("#").strip()
            if via.startswith("via "):
                via = via[4:]
            if via and via != "via":
                blocks[-1][2].add(via.split()[0].lower())
        else:
            blocks.append(("", [line], set()))
    gone = {d.lower() for d in drop}
    changed = True
    while changed:
        changed = False
        for name, _, wanted in blocks:
            if name and name not in gone and name not in KEEP and wanted and wanted <= gone:
                gone.add(name)
                changed = True
    return "\n".join(line for name, lines, _ in blocks if name not in gone for line in lines) + "\n"


if __name__ == "__main__":  # pins.py requirements.txt > trimmed.txt
    with open(sys.argv[1]) as f:
        sys.stdout.write(without_voice(f.read()))
