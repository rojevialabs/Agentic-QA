"""Incremental parser for the THOUGHT:/ACTION: reasoning-trace format (spec §9, roadmap contract C2)."""
import json
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


@dataclass(frozen=True)
class Thought:
    text: str


@dataclass(frozen=True)
class Action:
    data: dict[str, Any]


@dataclass(frozen=True)
class ParseError:
    raw: str
    reason: str


Event = Thought | Action | ParseError

_MD = r"[\s*_`#>\-]*"  # markdown decoration tolerated around markers
_THOUGHT_PREFIX = re.compile(rf"^{_MD}thought[*_`\s]*:[*_`\s]*", re.IGNORECASE)
_ACTION_ANYWHERE = re.compile(r"\bACTION[*_`\s]*:[*_`]*")  # uppercase marker, e.g. mid-line after a thought
_ACTION_LINE_START = re.compile(rf"^{_MD}action[*_`\s]*:[*_`]*", re.IGNORECASE | re.MULTILINE)


class _State(Enum):
    PREAMBLE = auto()
    THOUGHT = auto()
    ACTION = auto()
    DONE = auto()


def _balanced_object_end(text: str, start: int) -> int | None:
    """Index of the brace closing the object that opens at `start`, or None if it is not closed yet."""
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


class ThoughtActionParser:
    """Feed streamed text chunks; get Thought events per completed line and one Action (or ParseError)."""

    def __init__(self) -> None:
        self._state = _State.PREAMBLE
        self._line_buf = ""
        self._action_buf = ""

    @property
    def done(self) -> bool:
        return self._state is _State.DONE

    def feed(self, chunk: str) -> list[Event]:
        if self._state is _State.DONE:
            return []
        if self._state is _State.ACTION:
            self._action_buf += chunk
            return self._try_action()

        self._line_buf += chunk
        marker = self._find_action_marker(self._line_buf)
        if marker:
            events: list[Event] = []
            for line in self._line_buf[: marker.start()].splitlines():
                events += self._line(line)
            self._action_buf = self._line_buf[marker.end():]
            self._line_buf = ""
            self._state = _State.ACTION
            return events + self._try_action()

        *complete, self._line_buf = self._line_buf.split("\n")
        return [event for line in complete for event in self._line(line)]

    def finish(self) -> list[Event]:
        events: list[Event] = []
        if self._state in (_State.PREAMBLE, _State.THOUGHT):
            events += self._line(self._line_buf)
            events.append(ParseError("", "missing ACTION"))
        elif self._state is _State.ACTION:
            events.append(ParseError(self._action_buf, "incomplete ACTION JSON"))
        self._line_buf = self._action_buf = ""
        self._state = _State.DONE
        return events

    @staticmethod
    def _find_action_marker(text: str) -> re.Match[str] | None:
        matches = [m for m in (_ACTION_ANYWHERE.search(text), _ACTION_LINE_START.search(text)) if m]
        return min(matches, key=lambda m: m.start()) if matches else None

    def _line(self, line: str) -> list[Event]:
        text = line.strip()
        if not text:
            return []
        prefix = _THOUGHT_PREFIX.match(text)
        if prefix:
            self._state = _State.THOUGHT
            text = text[prefix.end():].strip()
            return [Thought(text)] if text else []
        return [Thought(text)] if self._state is _State.THOUGHT else []

    def _try_action(self) -> list[Event]:
        start = self._action_buf.find("{")
        if start == -1:
            return []
        end = _balanced_object_end(self._action_buf, start)
        if end is None:
            return []
        raw = self._action_buf[start : end + 1]
        self._state = _State.DONE
        try:
            return [Action(json.loads(raw))]
        except json.JSONDecodeError as exc:
            return [ParseError(raw, f"invalid ACTION JSON: {exc.msg}")]


def parse(text: str) -> list[Event]:
    """Parse a complete (non-streamed) response."""
    parser = ThoughtActionParser()
    return parser.feed(text) + parser.finish()
