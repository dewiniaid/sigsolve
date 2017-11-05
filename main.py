import itertools
import operator
import re
import time
from collections import defaultdict
from pathlib import Path

import pyautogui
from vision import Vision, BoardVision

from sigsolve.board import Tile, Tileset

pyautogui.FAILSAFE = True
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
#
# CARDINALS = {'water', 'air', 'earth', 'fire'}
# METALS = {}
# _last = None
# for _m in ('mercury', 'tin', 'iron', 'copper', 'silver', 'gold'):
#     METALS[_m] = _last
#     _last = _m
# del _last
# del _m


class GameState:
    def __init__(self, board):
        self.board = board
        self.stack = [MoveStackFrame(self)]
        self.best_run = []
        self.won = None
        self.bitmaps = set()

    def _checkwin(self):
        self.won = all(tile.element is None for tile in self.board.tiles) or None
        return self.won

    def step(self):
        move = self.stack[-1].advance()
        if move:
            if self._checkwin():
                self.best_run = list(frame.undo for frame in self.stack)
                return None
            self.stack.append(MoveStackFrame(self))
        if not move:
            self.stack.pop()
            if len(self.stack) > len(self.best_run):
                self.best_run = list(frame.undo for frame in self.stack)
            if not self.stack:
                self.won = False
                return None
            move = self.stack[-1].rewind()
        return move

    def simple_valid_moves(self):
        """
        Returns a list of valid moves.  Does not optimize.
        """
        moves = []

        board = self.board

        catalog = defaultdict(list)
        for tile in board.legal_tiles():
            element = tile.element
            catalog[element].append(tile)

        remaining_metals = board.remaining_metals()
        if remaining_metals:
            metal = remaining_metals[0]
            if metal.legal:
                if catalog['quicksilver']:
                    moves.extend(itertools.product(catalog['quicksilver'], [remaining_metals[0]]))
                elif len(remaining_metals) == 1:
                    return [[metal]]

        moves.extend(itertools.product(catalog['vitae'], catalog['mors']))
        for cardinal in self.board.CARDINALS:
            moves.extend(itertools.combinations(catalog[cardinal], 2))

        for cardinal in self.board.CARDINALS:
            moves.extend(itertools.product(catalog['salt'], catalog[cardinal]))

        moves.extend(itertools.combinations(catalog['salt'], 2))
        return moves

    def valid_moves(self):
        """
        Returns a list of valid moves.  If there are any moves which are guaranteed to be safe, the list will be truncated
        and only contain one of those moves.

        The following moves are considered 'safe':
        - Removing the last pair of mors and vitae from the board.
        - Removing gold from the board.
        - Removing the last quicksilver (and silver) from the board.
        - Removing the last salt when paired with the last of any elemental
        - Removing the last two salts when paired with the last one of two different elementals  (4 tiles total)
        - Removing the last two, four, six or eight copies of an element if all remaining salt has no illegal neighbors.
          (Or if there's no remaining salt)

        :param board: Tileset.
        :return: A list of lists with each tuple being tiles that are removed if that move is played.
        """
        board = self.board
        moves = []

        # Build list of all legal tiles.
        legal_tiles = board.legal_tiles()

        if not legal_tiles:
            return moves  # No legal moves!

        if len(legal_tiles) == 1 and (list(legal_tiles)[0].element != 'gold' or board.catalog['silver']):
            return moves  # No legal moves!

        # Catalog legal tiles by type.
        catalog = defaultdict(list)
        for tile in legal_tiles:
            element = tile.element
            catalog[element].append(tile)

        # Handle metals and quicksilver.
        # List of legal metals
        remaining_metals = board.remaining_metals()
        metals = list(itertools.takewhile(operator.attrgetter('legal'), remaining_metals))

        if metals:
            quicksilver = catalog['quicksilver']
            quicksilver_count = board.count('quicksilver')
            if quicksilver_count == len(quicksilver) and len(metals) >= len(remaining_metals)-1 and quicksilver_count >= len(remaining_metals)-1:
                # All quicksilver is playable, as are all metals (possibly excluding gold).  Lump that into one move.
                it = iter(metals)
                return [list(itertools.chain(*zip(quicksilver, it), it))]

            # Still here?  Well, there's no quicksilver fast exit, but we can add the combinations to the move list.
            # (If there's no quicksilver, this will be a noop)
            moves.extend(
                (tile, metals[0]) for tile in quicksilver
            )

        # Vitae/mors
        if catalog['vitae'] and len(catalog['vitae']) == board.count('vitae') and len(catalog['mors']) == board.count('mors'):
            # All vitae/mors visible, automatic moves.
            return [list(itertools.chain(*zip(catalog['vitae'], catalog['mors'])))]
        # Otherwise....
        moves.extend(itertools.product(catalog['vitae'], catalog['mors']))


        salts = catalog['salt']
        salts_legal = len(salts)
        salts_total = board.count('salt')
        salt_is_free = (salts_legal == salts_total)
        if salt_is_free:
            for tile in Tile.all_neighbors(board.catalog['salt']):
                if not tile.legal:
                    salt_is_free = False
                    break

        cardinal_counts = board.cardinal_counts()
        numodd = sum(count % 2 for count in cardinal_counts.values())
        if salt_is_free:
            if salts and not cardinal_counts:  # No elementals left.  Eliminate all salt.
                return [catalog['salt']]
        for element, count in cardinal_counts.items():
            # Check for cases of all remaining copies of an element being legal.
            if element not in catalog:
                continue
            tiles = catalog[element]
            numlegal = len(tiles)

            if numlegal == count and salt_is_free:
                result = tiles
                if count%2:  # Odd number, which means there must be at least one salt.
                    result = result + [catalog['salt'][0]]
                return [result]

            # Moves without salt.
            moves.extend(itertools.combinations(tiles, 2))  # May yield no items if numlegal == 1

            # Moves using salt.
            # Either this tile must be in an odd element, or there must be more salts than 1+the number of odd elements.

            if salts_total > numodd+1 or count%2:
                moves.extend(itertools.product(salts, tiles))

        if salts_legal - numodd >= 2:
            moves.extend(itertools.combinations(salts, 2))
        return moves


class MoveStackFrame:
    def __init__(self, gamestate):
        self.gamestate = gamestate
        self.undo = None
        # self.moves = set(tuple(move) for move in self.gamestate.valid_moves())
        # self.moves = self.gamestate.valid_moves()
        self.moves = self.gamestate.valid_moves()
        self.bitmap = None
        # print(repr(self.moves))

    def advance(self):
        while True:
            if not self.moves:
                return None
            move = self.moves.pop()
            self.undo = list((tile, tile.element) for tile in move)

            # Execute move
            for tile in move:
                tile.element = None

            # Generate and check against bitmap
            self.bitmap = self.gamestate.board.bitmap()
            if self.bitmap in self.gamestate.bitmaps:
                self.rewind()
                continue
            self.gamestate.bitmaps.add(self.bitmap)
            return self.undo
            #
            #
            #
            #
            # move = list((tile, None) for tile in move)
            #
            # # for frame in reversed(self.gamestate.stack[:-1]):
            # #     if move in frame.moves:
            # #         frame.moves.discard(move)
            # #     else:
            # #         break
            #
            # # for frame in self.gamestate.stack[:-2]:
            # # if len(self.gamestate.stack) > 1:
            # #     # If the parent has options A and B, it took A and we took B, there's no point in trying B on the parent
            # #     # should we fail.
            # #     self.gamestate.stack[-2].moves.discard(move)
            # #
            # #
            # self.undo = list((tile, tile.element) for tile in move)
            # move = list((tile, None) for tile in move)
            # self._execute(move)

    def rewind(self):
        undo = self.undo
        if not undo:
            return None
        self.undo = None
        for tile, element in undo:
            tile.element = element
        return undo

vision = None

def initialize():
    global vision
    print("Initializing...")

    datadir = Path('./data').resolve()
    vision = Vision(datadir / "empty.png")
    for file in datadir.glob('composite.*.png'):
        print(file.name)
        result = re.match(r'^composite\.(.*)\.png$', file.name.lower())
        if not result:
            continue
        vision.add_composite(result.group(1), file)

def click(where, down=0.05, up=0):
    x, y = where
    pyautogui.mouseDown(x=x, y=y)
    time.sleep(down)
    pyautogui.mouseUp(x=x, y=y)
    time.sleep(up)

def play_round():
    print("Scanning board...")

    board = Tileset()
    boardvision = BoardVision(vision)

    for tile in board.tiles:
        result = boardvision.match(tile)
        if result is None:
            tile.element = None
        else:
            tile.element = result.split('.')[0]

    for rownum, row in enumerate(board.rows[1:-1], start=1):
        results = []
        for tile in row[1:-1]:
            if tile is None:
                element = '##########'
            elif tile.element is None:
                element = ''
            else:
                element = tile.element
                if tile.legal:
                    element = element.upper()

            result = '{: ^10}'.format(element)
            results.append(result)

        ws = '     ' if rownum % 2 else ''
        outstr = '|'.join(results)
        print(f'[{rownum}] {ws}|{outstr}|')

    for element, tiles in board.catalog.items():
        ct = len(tiles)
        print(f'{element}: {ct} tile(s)')

    state = GameState(board)

    started = time.time()
    for steps in range(1, 1000000):
        if steps%10000 == 0:
            print(f"({steps} steps)")
        move = state.step()
        if move:
            continue
        if state.won:
            print(f"Victory after {steps} steps!  Winning moves are:")
        else:
            print(f"Loss after {steps} steps!  Best moveset was:")
        for num, move in enumerate(state.best_run, start=1):
            print(f"#{num}: {move!r}")
        break
    else:
        print("Ran out of steps.")
        print("Current stack:")
        for num, frame in enumerate(state.stack, start=1):
            print(f"#{num}: {frame.undo!r}")
        print("Best run:")
        for num, move in enumerate(state.best_run, start=1):
            print(f"#{num}: {move!r}")
        return state.won

    print("Elapsed time: ", time.time() - started)
    print("Bitmaps: ", len(state.bitmaps))
    print("Executing moveset in 5 seconds.   Ctrl+C to abort.")
    time.sleep(1)

    for num, move in enumerate(state.best_run, start=1):
        print(f"#{num}: {move!r}")
        for part, (tile, element) in enumerate(move, start=1):
            x, y = map(int, tile.rect.middle)
            print(f"... {part}: ({x:4},{y:4}) Clicking on {element}")
            click(tile.rect.middle, down=0.01, up=0.05)

    time.sleep(2.5)
    return state.won

if __name__ == '__main__':
    initialize()
    won = loss = timeout = 0
    while True:
        result = play_round()
        if result:
            won += 1
        elif result is False:
            loss += 1
        else:
            timeout = 0

        print(f"Won: {won} - Loss: {loss} - Timeout: {timeout}")

        click((890, 890), up=0.25)
        time.sleep(5)
