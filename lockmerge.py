"""Three-way merge for JSON lockfiles (stdlib only).

Lockfile format (JSON):
  {
    "name": "my-app",
    "dependencies": {
      "<dep-name>": {
        "version": "1.2.3",
        "requires": {"<dep-name>": "<semver-range>"}   // optional
      }
    }
  }

Merge is per dependency entry, not per line:
  - changed on one side only        -> take that side
  - changed identically on both     -> take it
  - changed differently on both     -> CONFLICT
  - deleted on one side, changed on the other -> CONFLICT (delete vs modify)
  - deleted on both                 -> stays deleted
  - added on both with same content -> keep; different content -> CONFLICT

When there are zero conflicts, the merged result is validated for
satisfiability: every `requires` range must be satisfied by the locked
version of the target dependency present in the merged lockfile.
"""

import argparse
import json
import sys

import semver


class LockfileError(ValueError):
    pass


class Conflict:
    """A per-dependency merge conflict."""

    def __init__(self, name, kind, base, ours, theirs, our_label, their_label):
        self.name = name
        self.kind = kind  # "both-modified" | "delete-vs-modify" | "both-added"
        self.base = base
        self.ours = ours
        self.theirs = theirs
        self.our_label = our_label
        self.their_label = their_label

    def to_dict(self):
        def view(entry):
            return None if entry is None else entry.get("version")

        return {
            "dependency": self.name,
            "kind": self.kind,
            "base_version": view(self.base),
            "versions": {
                self.our_label: view(self.ours),
                self.their_label: view(self.theirs),
            },
            "entries": {
                "base": self.base,
                self.our_label: self.ours,
                self.their_label: self.theirs,
            },
        }

    def render_text(self):
        d = self.to_dict()
        lines = [
            "CONFLICT (%s): %s" % (d["kind"], self.name),
            "  base:          %s" % (d["base_version"] or "<absent>"),
            "  %-14s %s"
            % (self.our_label + ":", d["versions"][self.our_label] or "<deleted>"),
            "  %-14s %s"
            % (
                self.their_label + ":",
                d["versions"][self.their_label] or "<deleted>",
            ),
        ]
        return "\n".join(lines)


def load_lockfile(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate_schema(data, source=path)
    return data


def validate_schema(data, source="<lockfile>"):
    if not isinstance(data, dict):
        raise LockfileError("%s: top level must be an object" % source)
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        raise LockfileError("%s: missing object field 'dependencies'" % source)
    for name, entry in deps.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
            raise LockfileError(
                "%s: dependency %r must be an object with a string 'version'"
                % (source, name)
            )
        semver.Version.parse(entry["version"])  # raises on invalid
        requires = entry.get("requires", {})
        if not isinstance(requires, dict):
            raise LockfileError(
                "%s: dependency %r has non-object 'requires'" % (source, name)
            )


def merge_lockfiles(base, ours, theirs, our_label="ours", their_label="theirs"):
    """Three-way merge. Returns (merged_lockfile_or_None, [Conflict])."""
    base_deps = base.get("dependencies", {})
    our_deps = ours.get("dependencies", {})
    their_deps = theirs.get("dependencies", {})

    merged = {}
    conflicts = []

    for name in sorted(set(base_deps) | set(our_deps) | set(their_deps)):
        b = base_deps.get(name)
        o = our_deps.get(name)
        t = their_deps.get(name)

        if o == t:
            # Same on both sides (includes deleted on both / untouched).
            if o is not None:
                merged[name] = o
            continue

        if b == o:
            # Only theirs changed (or we deleted, they kept -> theirs wins
            # only if they changed it; if they kept base and we deleted,
            # b == t handled below).
            if t is not None:
                merged[name] = t
            continue

        if b == t:
            # Only ours changed.
            if o is not None:
                merged[name] = o
            continue

        # Both sides changed differently. Distinguish delete-vs-modify.
        if o is None or t is None:
            kind = "delete-vs-modify"
        elif b is None:
            kind = "both-added"
        else:
            kind = "both-modified"
        conflicts.append(Conflict(name, kind, b, o, t, our_label, their_label))

    if conflicts:
        return None, conflicts

    result = dict(ours)
    result["dependencies"] = merged
    return result, []


def validate_satisfiability(lockfile):
    """Check every `requires` range against locked versions.

    Returns a list of problem strings; empty means satisfiable.
    """
    deps = lockfile.get("dependencies", {})
    problems = []
    for name in sorted(deps):
        entry = deps[name]
        for req_name in sorted(entry.get("requires", {})):
            req_range = entry["requires"][req_name]
            target = deps.get(req_name)
            if target is None:
                problems.append(
                    "%s requires %s@%s, but %s is not present in the lockfile"
                    % (name, req_name, req_range, req_name)
                )
                continue
            locked = target["version"]
            try:
                ok = semver.satisfies(locked, req_range)
            except semver.VersionError as exc:
                problems.append("%s requires %s@%s: %s" % (name, req_name, req_range, exc))
                continue
            if not ok:
                problems.append(
                    "%s requires %s@%s, but locked version is %s"
                    % (name, req_name, req_range, locked)
                )
    return problems


def build_report(conflicts, validation_problems=None):
    return {
        "status": "conflict" if conflicts else "clean",
        "conflict_count": len(conflicts),
        "conflicts": [c.to_dict() for c in conflicts],
        "validation": None
        if conflicts
        else {
            "satisfiable": not (validation_problems or []),
            "problems": validation_problems or [],
        },
    }


def cmd_merge(argv):
    parser = argparse.ArgumentParser(
        prog="lockmerge merge",
        description="Three-way merge of JSON lockfiles, per dependency.",
    )
    parser.add_argument("base")
    parser.add_argument("ours")
    parser.add_argument("theirs")
    parser.add_argument("-o", "--output", help="merged lockfile output path")
    parser.add_argument("--report", help="write JSON merge report to this path")
    parser.add_argument("--our-label", default="ours")
    parser.add_argument("--their-label", default="theirs")
    args = parser.parse_args(argv)

    base = load_lockfile(args.base)
    ours = load_lockfile(args.ours)
    theirs = load_lockfile(args.theirs)

    merged, conflicts = merge_lockfiles(
        base, ours, theirs, args.our_label, args.their_label
    )

    if conflicts:
        for conflict in conflicts:
            print(conflict.render_text(), file=sys.stderr)
        report = build_report(conflicts)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        print(
            "merge failed: %d conflict(s); no output written" % len(conflicts),
            file=sys.stderr,
        )
        return 1

    problems = validate_satisfiability(merged)
    report = build_report([], problems)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    if problems:
        for p in problems:
            print("UNSATISFIABLE: %s" % p, file=sys.stderr)
        return 2

    out = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    print(
        "merge clean: 0 conflicts; satisfiability check passed "
        "(%d dependencies)" % len(merged["dependencies"]),
        file=sys.stderr,
    )
    return 0


def cmd_validate(argv):
    parser = argparse.ArgumentParser(
        prog="lockmerge validate",
        description="Check a lockfile's version set for satisfiability.",
    )
    parser.add_argument("lockfile")
    args = parser.parse_args(argv)
    lockfile = load_lockfile(args.lockfile)
    problems = validate_satisfiability(lockfile)
    if problems:
        for p in problems:
            print("UNSATISFIABLE: %s" % p, file=sys.stderr)
        return 2
    print(
        "satisfiable: %d dependencies, 0 problems" % len(lockfile["dependencies"])
    )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: lockmerge <merge|validate> ...", file=sys.stderr)
        return 0 if argv else 2
    command, rest = argv[0], argv[1:]
    if command == "merge":
        return cmd_merge(rest)
    if command == "validate":
        return cmd_validate(rest)
    print("unknown command: %s" % command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
