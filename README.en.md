# 牵星 · StarFix

**语言 / Language:** [中文](README.md) · [English](README.en.md)

> Zheng He's fleet fixed its position by the stars; an AI fleet fixes its position by facts.

**One long-lived AI session (the captain) runs a fleet of terminal AI workers: dispatch, acceptance, merge, deploy, stakeholder relay, away-mode handover — and turns its judgement into rules you can assess.** Not a framework: a playbook that ran a real project for weeks, plus the scripts that hold it up.

## Run it in 5 minutes

tmux + python3 only:

```bash
export FLEET_HOME=/tmp/fleet
bash quickstart/demo.sh
```

A 15-step loop: dispatch → deliver → build → receipt → accept → ask → answer → unlock. Tutorial: [quickstart/README.en.md](quickstart/README.en.md).

## What it looks like

![Task lifecycle](docs/img/en/02-task-lifecycle.svg)

![Decision loop](docs/img/en/03-decision-loop.svg)

The full mechanism (roles, senses, context handoff, trajectory compiler, a real day) is in [docs/mechanism.en.md](docs/mechanism.en.md).

## Where to read

| You want to know | Go to |
|---|---|
| Why the captain may decide on its own; the three things that must be escalated | [doctrine/00 — the captain's soul](doctrine/00-README.en.md) |
| First actions on taking office; the full rules | [skill/SKILL.en.md](skill/SKILL.en.md) |
| Why a fleet beats spawned sub-agents; how many tokens the trajectory compiler saves | [docs/why.en.md](docs/why.en.md) |
| Monitors / terminal delivery / task activator / away mode / memory / context handoff | [specs/](specs/) |
| How judgement goes wrong and how tools lie | [doctrine/02 case files](doctrine/02-试错战例集.en.md) · [doctrine/04 pitfalls](doctrine/04-踩坑指南.en.md) |
| How to assess a captain | [doctrine/03](doctrine/03-舰长考核与benchmark方向.en.md) · [benchmarks/captain-v1](benchmarks/captain-v1/README.en.md) |
| The machine auditing the process | [docs/trajectory.en.md](docs/trajectory.en.md) · [trajectory/](trajectory/) |
| Scripts and environment variables | [scripts/README-env.en.md](scripts/README-env.en.md) |
| Glossary (zh ↔ en) | [docs/glossary.md](docs/glossary.md) |

## Who it is for

- **Forward Deployed Engineers**: one person driving many parallel workstreams on a customer site with an AI fleet — dispatching, accepting, relaying to customer roles, getting decisions to the customer's boss, leaving an auditable trail, handing over at any time.
- Independent developers and small-team tech leads who want several AI sessions to cooperate for a long time without losing control.

## Six principles

1. The captain judges, never writes business code; crew failure is normal, captain self-inflicted incidents are the real failures.
2. A receipt is not a fact, the artifact is; every criterion must answer "under what condition would it go red".
3. Stop and ask the Owner only for irreversible production actions, business facts only a human knows, and outward publication.
4. Every pending question goes on the decision panel; an answer unlocks tasks.
5. Monitors report only the dangerous side and only transitions, and never poll.
6. Lessons become case files and gates the same day; overnight they are lost.

## Before sharing

`tools/scrub-gate.sh` must pass (the term list and salt live outside the repo; without them the gate degrades to structural checks only).

## Author

**三娃老爸** (GitHub MarcMao0819; Xiaohongshu "三娃老爸", ID 294613559). The mechanism, the rules and every ruling behind them were set by him on a real project; the captain (a Claude session) executes under his command — porting, scrubbing, drawing, committing. Questions welcome; a follow is appreciated.

MIT — see [LICENSE](LICENSE).
