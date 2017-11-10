"""
Logic for solving:

At each level:
    Generate a list of possible moves, where a move is a list of tuples (tile, element)
    Pop the last move off of the list and onto a stack and repeat the process.

    If the list is empty, pop the stack to undo to the previous level.

    Moves are usually just two tiles, but some 'automatic' moves may be bundled in to the list to be handled as one
    transaction.

    Moves are rated in priority order:


AUTOMATIC
    Performing the only legal move if only one move is legal
    Any move that affects the last tile of one of these types: gold, mors, vitae, quicksilver, iron
    Removing the last two tiles of an element from the board if there is no remaining salt.
    Removing a lone salt and lone element.
    Removing two lone elements using the last two salts.
    Removing the last two tiles of an element from the board if all salt is accessible. ** (This may be gamechanging?)

NORMAL
    Sorted by position in this list, then by total number of affected tile legality states.  Exception: If the number
    of legality states is zero, drops to LOW.
    Removing quicksilver + metal
    Removing vitae + mors
    Removing a pair of elements
    Removing a salt and an element when that element's count is odd.
    Removing a salt and an element when that element's count is even.

LOW
    Removing two salts

FORBIDDEN
    Never allowed to happen.
    Removing a salt and an element when the number of remaining salts is equal to the number of elements that have an
    odd number.

"""
import itertools
import operator
from collections import defaultdict

from sigsolve.board import Tile


class SolverFrame:
    def __init__(self, solver, bitmap):
        self.solver = solver
        self.board = solver.board
        self.move = []
        self.moves = self.solver.valid_moves()
        self.bitmap = bitmap

    @staticmethod
    def _execute(tiles, exists):
        for tile in tiles:
            tile.exists = exists

    def run(self):
        if self.move:
            self._execute(self.move, True)
            self.move = []

        while self.moves:
            move = self.moves.pop()
            bits = ~Tile.bitmap(move)
            bitmap = self.bitmap & bits
            if bitmap in self.solver.bitmaps:
                self.solver.bitmap_hits += 1
                continue
            self.move = move
            self._execute(move, False)
            return bitmap
        return None


class Solver:
    def __init__(self, board):
        self.board = board
        self.stack = None
        self.solution = []  # Or best attempt.
        self.won = None
        self.bitmaps = set()
        self.bitmap_hits = 0
        self.iterations = 0

    def save_solution(self):
        self.solution = [frame.move for frame in self.stack]
        return self.solution

    def solve(self, steps=None):
        self.won = None
        if self.stack is None:
            bitmap = self.board.bitmap()
            self.iterations = 0
            self.bitmaps = {bitmap}
            self.bitmap_hits = 0
            self.stack = [SolverFrame(self, bitmap)]

        while steps is None or steps > 0:
            self.iterations += 1
            top = self.stack[-1]
            if steps:
                steps -= 1
            bitmap = top.run()

            if bitmap:  # Successfully made a move, additional moves exist
                self.bitmaps.add(bitmap)
                self.stack.append(SolverFrame(self, bitmap))
                continue
            if bitmap is None:  # Failed to make a move, so backtracking.
                if len(self.stack) > len(self.solution):  # Save new best attempt if it is one.
                    self.save_solution()
                self.stack.pop()
                if not self.stack:
                    self.won = False
                    return False  # Loss condition triggered.
                continue

            # Still here, so bitmap was zero meaning no tiles exist after doing this move.
            # WE WIN!
            self.save_solution()
            self.won = True
            return True
        # Ran out of steps
        return None

    def valid_moves(self):
        """
        Returns a list of valid moves.  If there are any moves which are guaranteed to be safe, the list will be
        truncated and only contain one of those moves (or multiple safe moves condensed into a single move)

        The following moves are considered 'safe', in order of checking:
        - Removing gold from the board.
        - Removing all remaining quicksilver and metals (possibly excluding gold) from the board, if all of those tiles
            are reachable.
        - Removing all remaining vitae and mors from the board if all of those tiles are reachable.
        - Removing all remaining salt if all of those tiles are reachable and no cardinals are left.
        - Removing all remaining copies of a cardinal if none of the remaining salt blocks access to other tiles.  This
            will include one of the remaining salts if the number of remaining copies is odd.
        :return: A list of moves, where each move is a list of tiles to remove in order.
        """
        board = self.board
        moves = []

        # Build list of all legal tiles.
        legal_tiles = board.legal_tiles()

        if not legal_tiles:
            return moves  # No legal moves!

        # Fast exit for gold, if it's gold...
        if len(legal_tiles) == 1:
            if legal_tiles[0].element == 'gold' and not board.catalog['silver']:
                return [legal_tiles]
            return moves  # No legal moves!

        # Catalog legal tiles by type.
        catalog = defaultdict(list)
        for tile in legal_tiles:
            element = tile.element
            catalog[element].append(tile)

        # Handle metals and quicksilver.
        # List of legal metals
        remaining_metals = board.remaining_metals()
        legal_metals = list(itertools.takewhile(operator.attrgetter('legal'), remaining_metals))

        if legal_metals:
            quicksilver = catalog['quicksilver']
            quicksilver_count = board.remaining('quicksilver')
            if (
                    quicksilver_count == len(quicksilver)  # all the quicksilver
                    and len(legal_metals) >= len(remaining_metals)-1  # enough for all the metals (except maybe gold)
                    and quicksilver_count >= len(remaining_metals)-1  # can play all the metals (except maybe gold)
            ):
                # All quicksilver is playable, as are all metals (possibly excluding gold).  Lump that into one move.
                it = iter(legal_metals)
                return [list(itertools.chain(*zip(quicksilver, it), it))]

            # Still here?  Well, there's no quicksilver fast exit, but we can add the combinations to the move list.
            # (If there's no quicksilver, this will be a noop)
            moves.extend((tile, legal_metals[0]) for tile in quicksilver)

        # Vitae/mors
        if catalog['vitae'] and catalog['mors']:
            if len(catalog['vitae']) == board.remaining('vitae') and len(catalog['mors']) == board.remaining('mors'):
                # All vitae/mors visible, automatic moves.
                return [list(itertools.chain(*zip(catalog['vitae'], catalog['mors'])))]
            # Some but not all playable.
            moves.extend(itertools.product(catalog['vitae'], catalog['mors']))

        # Cardinals, salts, and quintessence
        salts = catalog['salt']
        salts_legal = len(salts)
        salts_total = board.remaining('salt')
        salt_is_free = (salts_legal == salts_total)  # All salt is playable and has no impact on legality.
        cardinal_counts = board.remaining_cardinals()  # Data on how many cardinal elements are left.
        if salt_is_free:
            # If all salt is reachable, there's a multiple of two, and no cardinal elements are left, we can eliminate
            # all salt.
            if not (len(salts) or any(cardinal_counts.values())):
                return [catalog['salt']]

            # Otherwise, see if the salt affects legality of any tiles.
            for tile in Tile.all_neighbors(board.catalog['salt']):
                if tile.exists and not tile.legal:
                    salt_is_free = False
                    break

        quints = catalog['quintessence']
        quints_legal = len(quints)
        quints_total = board.remaining('quintessence')

        # Quintessence usage.
        if quints_legal:
            moves.extend(itertools.product(quints, *(catalog[e] for e in board.CARDINALS)))

        odd_cardinals = sum(count % 2 for count in cardinal_counts.values())  # How many cardinals have an odd number?
        for element, count in cardinal_counts.items():
            # Check for cases of all remaining copies of an element being legal.
            if element not in catalog:
                continue
            tiles = catalog[element]
            legal_count = len(tiles)
            odd = count % 2

            if legal_count == count and salt_is_free and not quints_total:
                if odd:
                    if catalog['salt']:  # Odd number, which means hopefully there is one salt.
                        tiles.append(catalog['salt'][0])  # Destructive to the catalog, but it's about to not matter.
                        return [tiles]
                else:
                    return [tiles]

            # Moves without salt.
            moves.extend(itertools.combinations(tiles, 2))  # May yield no items if numlegal == 1

            # Moves using salt.
            # Either this tile must have an odd count, or there must be more salts than 1+the number of odd elements.
            if salts_total > odd_cardinals+1 or odd:
                moves.extend(itertools.product(salts, tiles))

        # Finally, combinations of salt.
        if (salts_legal - odd_cardinals >= 2) or not quints_total:
            moves.extend(itertools.combinations(salts, 2))

        return moves
