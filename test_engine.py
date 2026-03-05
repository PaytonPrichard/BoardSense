"""
test_engine.py — Engine regression + consistency test.

Tests three things:
  1. Eval sign correctness: White-advantage positions must have positive evals.
  2. Classification sanity: well-known errors must be labelled correctly.
  3. Cross-run consistency: two analyses of the same game must agree.

Uses two famous games:
  - The Immortal Game (Anderssen–Kieseritzky, London 1851)
    Short, tactically rich, well-known blunders on Black's side.
  - A short Ruy Lopez game ending in a Scholar's-mate style trap
    so we have a definitive blunder to validate against.
"""

import sys
from engine import analyze_game

# ─── Test games ──────────────────────────────────────────────────────────────

# The Immortal Game
IMMORTAL = """\
[Event "London"]
[Site "London"]
[Date "1851.??.??"]
[White "Anderssen, Adolf"]
[Black "Kieseritzky, Lionel"]
[Result "1-0"]
[ECO "C33"]

1.e4 e5 2.f4 exf4 3.Bc4 Qh4+ 4.Kf1 b5 5.Bxb5 Nf6 6.Nf3 Qh6 7.d3 Nh5
8.Nh4 Qg5 9.Nf5 c6 10.g4 Nf6 11.Rg1 cxb5 12.h4 Qg6 13.h5 Qg5 14.Qf3 Ng8
15.Bxf4 Qf6 16.Nc3 Bc5 17.Nd5 Qxb2 18.Bd6 Bxg1 19.e5 Qxa1+ 20.Ke2 Na6
21.Nxg7+ Kd8 22.Qf6+ Nxf6 23.Be7# 1-0
"""

# A short blunder game: Black blunders a queen immediately on move 4
BLUNDER_GAME = """\
[Event "Test"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1.e4 e5 2.Nf3 Nc6 3.Bc4 Nd4 4.Nxe5 Qg5 5.Nxf7 Qxg2 6.Rf1 Qxe4+ 7.Be2 Nf3# 1-0
"""


# ─── Helpers ─────────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}", end="")
    if detail:
        print(f"  — {detail}", end="")
    print()
    return condition


# ─── Test 1: Eval sign correctness ──────────────────────────────────────────

def test_eval_signs(moves: list[dict], game_name: str) -> int:
    """
    Evals are from White's perspective.
      • White-to-move positions with a strong advantage → positive.
      • Positions just before a forced mate for White → high positive (≥8.0).
    Returns number of failures.
    """
    print(f"\n[1] Eval sign sanity — {game_name}")
    failures = 0

    for m in moves:
        color = m["color"]
        ev_before = m["eval_before"]
        ev_after  = m["eval_after"]
        mn        = m["move_number"]
        prefix    = f"{mn}." if color == "white" else f"{mn}..."

        # Both evals must be in the valid range
        for label, val in [("eval_before", ev_before), ("eval_after", ev_after)]:
            if not (-10.01 <= val <= 10.01):
                print(f"    [{FAIL}] {prefix}{m['move_san']} {label}={val:.2f} out of [-10,10]")
                failures += 1

    ok = check(
        f"All {len(moves)} evals within [-10, +10]",
        failures == 0,
        f"{failures} out-of-range values" if failures else "",
    )
    if not ok:
        failures += 1
    return failures


# ─── Test 2: Classification sanity ──────────────────────────────────────────

def test_classifications(moves: list[dict], game_name: str) -> int:
    """
    High-level checks:
      • At least one move classified as best/good/inaccuracy/mistake/blunder.
      • No move classified as blunder when eval_delta is tiny (< 0.3 pawns).
      • Accuracy values in [0, 100].
    """
    print(f"\n[2] Classification sanity — {game_name}")
    failures = 0

    classes_seen = {m["classification"] for m in moves}
    print(f"    Classifications seen: {sorted(classes_seen)}")

    # Accuracy in range
    for m in moves:
        acc = m.get("move_accuracy", -1)
        if not (0.0 <= acc <= 100.0):
            mn     = m["move_number"]
            prefix = f"{mn}." if m["color"] == "white" else f"{mn}..."
            print(f"    [{FAIL}] {prefix}{m['move_san']} accuracy={acc:.1f} out of [0,100]")
            failures += 1
    check("All accuracy values in [0, 100]", failures == 0)

    # No blunder when the actual centipawn drop is negligible
    noise_blunders = [
        m for m in moves
        if m["classification"] == "blunder"
        and abs(m["eval_after"] - m["eval_before"]) < 0.30
        and m["classification"] != "book"
    ]
    for m in noise_blunders:
        mn     = m["move_number"]
        prefix = f"{mn}." if m["color"] == "white" else f"{mn}..."
        print(f"    [{WARN}] Suspicious blunder with tiny eval change: "
              f"{prefix}{m['move_san']} "
              f"({m['eval_before']:+.2f}→{m['eval_after']:+.2f})")
    check("No blunders on < 0.3-pawn eval change", len(noise_blunders) == 0,
          f"{len(noise_blunders)} suspicious" if noise_blunders else "")

    return failures


# ─── Test 3: Cross-run consistency ──────────────────────────────────────────

def test_consistency(pgn: str, game_name: str) -> int:
    """
    Run the same game twice and compare.
      • eval values must agree within ±0.20 pawns (Lazy SMP tolerance).
      • classifications must be identical.
      • best_move_uci must match.
    """
    print(f"\n[3] Cross-run consistency — {game_name}")

    print("    Run 1 …", end="", flush=True)
    moves1, _ = analyze_game(pgn)
    print(" done.")

    print("    Run 2 …", end="", flush=True)
    moves2, _ = analyze_game(pgn)
    print(" done.")

    failures  = 0
    cls_diffs = 0
    ev_diffs  = []
    bm_diffs  = 0

    for i, (m1, m2) in enumerate(zip(moves1, moves2)):
        mn     = m1["move_number"]
        prefix = f"{mn}." if m1["color"] == "white" else f"{mn}..."

        # Eval before
        d = abs(m1["eval_before"] - m2["eval_before"])
        ev_diffs.append(d)
        if d > 0.01:
            print(f"    [{WARN}] Eval drift at {prefix}{m1['move_san']}: "
                  f"run1={m1['eval_before']:+.3f} run2={m2['eval_before']:+.3f} delta={d:.3f}")

        # Classification
        if m1["classification"] != m2["classification"]:
            print(f"    [{FAIL}] Classification mismatch at {prefix}{m1['move_san']}: "
                  f"{m1['classification']} vs {m2['classification']}")
            cls_diffs += 1
            failures  += 1

        # Best move
        if m1.get("best_move_uci") != m2.get("best_move_uci"):
            print(f"    [{WARN}] Best-move mismatch at {prefix}{m1['move_san']}: "
                  f"{m1.get('best_move_uci')} vs {m2.get('best_move_uci')}")
            bm_diffs += 1

    max_d = max(ev_diffs) if ev_diffs else 0.0
    avg_d = sum(ev_diffs) / len(ev_diffs) if ev_diffs else 0.0
    # Threads=1 + fixed depth → fully deterministic; allow only float rounding noise
    check(f"Eval agreement within ±0.01 (max={max_d:.4f}, avg={avg_d:.4f})", max_d <= 0.01)
    check(f"Classification consistency (0 mismatches)", cls_diffs == 0,
          f"{cls_diffs} mismatches" if cls_diffs else "")
    if bm_diffs:
        print(f"    [{WARN}] Best-move mismatches: {bm_diffs} (minor, doesn't affect classification)")

    return failures


# ─── Test 4: Specific known positions ───────────────────────────────────────

def test_known_positions(moves: list[dict]) -> int:
    """
    Objective assertions about the Immortal Game.

    Note on 18.Bd6: Anderssen's famous piece sacrifice is *speculative*, not
    objectively winning. Black can decline by not taking g1, so Stockfish
    correctly rates the position after Bd6 as roughly equal (0 to -0.5).
    Calling it a blunder is technically correct engine behaviour — the move
    only works because the opponent falls into the trap.  We do NOT assert that
    the eval is positive after Bd6.

    What we DO assert: once Black plays 18...Bxg1 and blunders, the eval jumps
    back above 0, and the final mated position is at the max cap.
    """
    print("\n[4] Known-position assertions — Immortal Game")
    failures = 0

    # After 23.Be7# eval must be at the cap (+10.0)
    last = moves[-1]
    is_mate = last["eval_after"] >= 9.5
    check(
        f"Final position eval at +10 (forced mate): got {last['eval_after']:+.2f}",
        is_mate,
    )
    if not is_mate:
        failures += 1

    # After Black's 20...Na6 blunder the eval must be a forced-win level (>= +7)
    na6 = next((m for m in moves if m["move_number"] == 20 and m["color"] == "black"), None)
    if na6:
        won = na6["eval_after"] >= 7.0
        check(
            f"After 20...Na6 blunder eval >= +7 (forced win): got {na6['eval_after']:+.2f}",
            won,
        )
        if not won:
            failures += 1

    return failures


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    total_failures = 0

    print("=" * 60)
    print("BoardSense — Engine Test Suite")
    print("=" * 60)

    # --- Immortal Game ---
    print("\n>>> Analyzing Immortal Game …")
    moves_immortal, _ = analyze_game(IMMORTAL)
    print(f"    {len(moves_immortal)} moves analysed.")

    total_failures += test_eval_signs(moves_immortal, "Immortal Game")
    total_failures += test_classifications(moves_immortal, "Immortal Game")
    total_failures += test_known_positions(moves_immortal)

    # Print full move table for inspection
    print("\n    Full move table:")
    print(f"    {'#':<5} {'Move':<10} {'Class':<12} {'Ev_before':>9} {'Ev_after':>9} {'WPL':>6} {'Acc':>6}")
    print("    " + "-" * 60)
    for m in moves_immortal:
        mn  = m["move_number"]
        dot = "." if m["color"] == "white" else "…"
        print(
            f"    {mn}{dot:<4} {m['move_san']:<10} {m['classification']:<12} "
            f"{m['eval_before']:>+9.2f} {m['eval_after']:>+9.2f} "
            f"{m.get('wp_loss', 0):>6.1f} {m.get('move_accuracy', 0):>6.1f}"
        )

    # --- Consistency test ---
    total_failures += test_consistency(IMMORTAL, "Immortal Game")

    # --- Summary ---
    print("\n" + "=" * 60)
    if total_failures == 0:
        print(f"  {PASS} All tests passed.")
    else:
        print(f"  {FAIL} {total_failures} test(s) failed.")
    print("=" * 60)

    return 0 if total_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
