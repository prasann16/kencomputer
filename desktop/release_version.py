"""Release versions shared by manual beta and weekly stable preparation."""
import argparse
import re
import subprocess


VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-beta\.([1-9][0-9]*))?")


def parse(tag):
    match = VERSION.fullmatch(tag)
    if not match:
        raise ValueError("Use vX.Y.Z for stable or vX.Y.Z-beta.N for beta (N starts at 1).")
    major, minor, patch, beta = match.groups()
    return (int(major), int(minor), int(patch)), int(beta) if beta else None


def next_tag(tags, channel, head_tags=()):
    versions = {tag: parse(tag) for tag in tags if VERSION.fullmatch(tag)}
    stable = [v for v, beta in versions.values() if beta is None]
    latest = max(stable, default=(0, 0, 0))
    if channel == "stable":
        existing = [tag for tag in head_tags if tag in versions and versions[tag][1] is None]
        if existing:
            return max(existing, key=lambda tag: versions[tag][0])
        return "v%d.%d.%d" % (*latest[:2], latest[2] + 1)
    target = max([(*latest[:2], latest[2] + 1), *(v for v, beta in versions.values() if beta is not None)])
    betas = [beta for v, beta in versions.values() if v == target and beta is not None]
    return "v%d.%d.%d-beta.%d" % (*target, max(betas, default=0) + 1)


def check_beta(tag, tags):
    version, beta = parse(tag)
    if beta is None:
        raise ValueError("The manual beta workflow requires a -beta.N version.")
    stable = [parse(t)[0] for t in tags if VERSION.fullmatch(t) and parse(t)[1] is None]
    if stable and version <= max(stable):
        raise ValueError("A beta must target a version newer than the latest stable tag.")
    betas = [parse(t) for t in tags if VERSION.fullmatch(t) and parse(t)[1] is not None]
    if betas and (version, beta) < max(betas):
        raise ValueError("A beta must not go backwards from the latest beta tag.")
    return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=["stable", "beta", "classify"])
    parser.add_argument("tag", nargs="?", default="")
    args = parser.parse_args()
    try:
        if args.channel == "classify":
            print("beta" if parse(args.tag)[1] is not None else "stable")
            return
        if args.channel == "stable" and args.tag:
            raise ValueError("Stable tags are chosen automatically; omit the version.")
        tags = subprocess.check_output(["git", "tag", "-l"], text=True).splitlines()
        head_tags = subprocess.check_output(["git", "tag", "--points-at", "HEAD"], text=True).splitlines()
        print(check_beta(args.tag, tags) if args.tag else next_tag(tags, args.channel, head_tags))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
