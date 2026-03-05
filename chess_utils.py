"""
BoardSense - chess_utils.py
Shared chess logic used by both app.py and curriculum.py.
Kept separate to avoid circular imports.
"""

import chess


def position_has_concept(fen: str, concept: str, best_move_san: str, player_color: str) -> bool:
    """
    Return True only if this position genuinely illustrates the named chess concept.

    Uses python-chess to detect structural and tactical patterns.
    Returns False for concepts that cannot be reliably detected — the caller
    will then exclude the puzzle (better no puzzle than an irrelevant one).
    """
    try:
        board = chess.Board(fen)
    except Exception:
        return False

    cn = concept.lower().strip()
    mover = chess.WHITE if player_color == "white" else chess.BLACK

    # ── Pawn structure ────────────────────────────────────────────────────────

    if cn == "doubled pawns":
        for c in (chess.WHITE, chess.BLACK):
            pawns = int(board.pieces(chess.PAWN, c))
            for f in range(8):
                if chess.popcount(pawns & int(chess.BB_FILES[f])) >= 2:
                    return True
        return False

    if cn == "isolated pawn":
        for c in (chess.WHITE, chess.BLACK):
            pawns = board.pieces(chess.PAWN, c)
            p_int = int(pawns)
            for sq in pawns:
                f = chess.square_file(sq)
                neighbors = 0
                if f > 0: neighbors |= int(chess.BB_FILES[f - 1])
                if f < 7: neighbors |= int(chess.BB_FILES[f + 1])
                if not (p_int & neighbors):
                    return True
        return False

    if cn == "passed pawn":
        for c in (chess.WHITE, chess.BLACK):
            pawns    = board.pieces(chess.PAWN, c)
            op_pawns = int(board.pieces(chess.PAWN, not c))
            for sq in pawns:
                f    = chess.square_file(sq)
                rank = chess.square_rank(sq)
                ahead = 0
                for adj_f in [f - 1, f, f + 1]:
                    if 0 <= adj_f <= 7:
                        r_range = range(rank + 1, 8) if c == chess.WHITE else range(0, rank)
                        for r in r_range:
                            ahead |= int(chess.BB_SQUARES[chess.square(adj_f, r)])
                if not (op_pawns & ahead):
                    return True
        return False

    # backward pawn, pawn island, minority attack — too complex to detect reliably
    if cn in ("backward pawn", "pawn island", "minority attack"):
        return False

    # ── Piece play ────────────────────────────────────────────────────────────

    if cn == "rook on open file":
        all_pawns = int(board.pawns)
        for c in (chess.WHITE, chess.BLACK):
            for sq in board.pieces(chess.ROOK, c):
                if not (all_pawns & int(chess.BB_FILES[chess.square_file(sq)])):
                    return True
        return False

    if cn == "rook on seventh rank":
        for c in (chess.WHITE, chess.BLACK):
            target_rank = 6 if c == chess.WHITE else 1
            for sq in board.pieces(chess.ROOK, c):
                if chess.square_rank(sq) == target_rank:
                    return True
        return False

    if cn == "bad bishop":
        for c in (chess.WHITE, chess.BLACK):
            bishops = board.pieces(chess.BISHOP, c)
            pawns   = board.pieces(chess.PAWN, c)
            n_pawns = chess.popcount(int(pawns))
            if n_pawns < 2:
                continue
            for sq in bishops:
                bcolor = (chess.square_file(sq) + chess.square_rank(sq)) % 2
                same = sum(
                    1 for p in pawns
                    if (chess.square_file(p) + chess.square_rank(p)) % 2 == bcolor
                )
                if same >= n_pawns * 0.55:
                    return True
        return False

    if cn == "bishop pair":
        for c in (chess.WHITE, chess.BLACK):
            if chess.popcount(int(board.pieces(chess.BISHOP, c))) >= 2:
                return True
        return False

    if cn in ("outpost", "knight outpost"):
        for c in (chess.WHITE, chess.BLACK):
            opp_pawns = board.pieces(chess.PAWN, not c)
            for sq in board.pieces(chess.KNIGHT, c):
                rank = chess.square_rank(sq)
                in_opp_half = (c == chess.WHITE and rank >= 4) or (c == chess.BLACK and rank <= 3)
                if not in_opp_half:
                    continue
                f = chess.square_file(sq)
                # Can the opponent attack this square with a pawn?
                if c == chess.WHITE:
                    # Check if any black pawn can attack this square (black pawns attack downward)
                    attack_sqs = []
                    if f > 0 and rank < 7: attack_sqs.append(chess.square(f - 1, rank + 1))
                    if f < 7 and rank < 7: attack_sqs.append(chess.square(f + 1, rank + 1))
                else:
                    # Check if any white pawn can attack this square (white pawns attack upward)
                    attack_sqs = []
                    if f > 0 and rank > 0: attack_sqs.append(chess.square(f - 1, rank - 1))
                    if f < 7 and rank > 0: attack_sqs.append(chess.square(f + 1, rank - 1))
                if not any(board.piece_at(s) == chess.Piece(chess.PAWN, not c) for s in attack_sqs):
                    return True
        return False

    if cn == "piece activity":
        return True  # any position qualifies

    # ── Tactical concepts ─────────────────────────────────────────────────────

    if cn == "pin":
        for c in (chess.WHITE, chess.BLACK):
            for sq in chess.scan_reversed(int(board.occupied_co[c])):
                if board.is_pinned(c, sq):
                    return True
        return False

    if cn == "fork":
        # Best move creates a fork: the moved piece attacks 2+ opponent pieces
        try:
            mv   = board.parse_san(best_move_san)
            test = board.copy()
            test.push(mv)
            dest = mv.to_square
            opp  = int(test.occupied_co[not mover])
            return chess.popcount(int(test.attacks(dest)) & opp) >= 2
        except Exception:
            return False

    if cn == "back rank weakness":
        for c in (chess.WHITE, chess.BLACK):
            king_sq = board.king(c)
            if king_sq is None:
                continue
            back_rank = 0 if c == chess.WHITE else 7
            if chess.square_rank(king_sq) != back_rank:
                continue
            # King is on back rank — check if it has no pawn shield
            kf = chess.square_file(king_sq)
            shield_rank = 1 if c == chess.WHITE else 6
            has_shield = any(
                board.piece_at(chess.square(f, shield_rank)) == chess.Piece(chess.PAWN, c)
                for f in range(max(0, kf - 1), min(8, kf + 2))
            )
            if not has_shield:
                return True
        return False

    if cn == "trapped piece":
        for c in (chess.WHITE, chess.BLACK):
            for sq in chess.scan_reversed(int(board.occupied_co[c])):
                p = board.piece_at(sq)
                if p and p.piece_type not in (chess.KING, chess.PAWN):
                    mobility = sum(1 for m in board.legal_moves if m.from_square == sq)
                    if mobility <= 1:
                        return True
        return False

    # ── Endgame concepts ──────────────────────────────────────────────────────

    if cn == "opposition":
        wk = board.king(chess.WHITE)
        bk = board.king(chess.BLACK)
        if wk is not None and bk is not None:
            rd = abs(chess.square_rank(wk) - chess.square_rank(bk))
            fd = abs(chess.square_file(wk) - chess.square_file(bk))
            # Direct or diagonal opposition
            if (rd == 2 and fd == 0) or (rd == 0 and fd == 2) or (rd == 2 and fd == 2):
                return True
        return False

    if cn == "bishop vs knight":
        b_side = n_side = None
        for c in (chess.WHITE, chess.BLACK):
            has_b = chess.popcount(int(board.pieces(chess.BISHOP, c))) > 0
            has_n = chess.popcount(int(board.pieces(chess.KNIGHT, c))) > 0
            if has_b and not has_n:
                b_side = c
            if has_n and not has_b:
                n_side = c
        return b_side is not None and n_side is not None and b_side != n_side

    # All remaining concepts (Discovered Attack, Double Check, Deflection, Decoy,
    # Overloading, Zwischenzug, X-Ray Attack, Two Weaknesses, Space Advantage,
    # Prophylaxis, King Safety, Centralization, Initiative, Zugzwang,
    # Triangulation, Lucena Position, Philidor Position, Skewer) cannot be
    # reliably detected in a static position — return False so no irrelevant
    # puzzles are shown.
    return False
