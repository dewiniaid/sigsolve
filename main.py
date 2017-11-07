import itertools
import operator
import re
import time
from collections import defaultdict
from pathlib import Path
import argparse
import pyautogui
import textwrap
import concurrent.futures
import os

from sigsolve.board import Tile, Board
from sigsolve.vision import Vision

import logging
logging.basicConfig()  # level=logging.DEBUG)
log = logging.getLogger('main')
log.setLevel(logging.DEBUG)
# logging.getLogger('PIL').setLevel(logging.INFO)
# logging.getLogger('pyscreenshot').setLevel(logging.INFO)
# pil_log.setLevel(logging.INFO)

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
                    return False  # Loss condition triggered.
                continue

            # Still here, so bitmap was zero meaning no tiles exist after doing this move.
            # WE WIN!
            self.save_solution()
            return True
        # Ran out of steps
        return None

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

        :param board: Board.
        :return: A list of lists with each tuple being tiles that are removed if that move is played.
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

        # Cardinals and salts.
        salts = catalog['salt']
        salts_legal = len(salts)
        salts_total = board.remaining('salt')
        salt_is_free = (salts_legal == salts_total)  # All salt is playable and has no impact on legality.
        if salt_is_free:
            for tile in Tile.all_neighbors(board.catalog['salt']):
                if tile.exists and not tile.legal:
                    salt_is_free = False
                    break

        cardinal_counts = board.remaining_cardinals()  # Data on how many cardinal elements are left.
        odd_cardinals = sum(count % 2 for count in cardinal_counts.values())  # How many cardinals have an odd number?
        if salt_is_free:
            if salts and not cardinal_counts:  # No cardinals left.  Eliminate all salt.
                return [catalog['salt']]
        for element, count in cardinal_counts.items():
            # Check for cases of all remaining copies of an element being legal.
            if element not in catalog:
                continue
            tiles = catalog[element]
            legal_count = len(tiles)
            odd = count % 2

            if legal_count == count and salt_is_free:
                if odd:  # Odd number, which means there must be at least one salt... and all salt is playable
                    tiles.append(catalog['salt'][0])  # Destructive to the catalog, but it's about to not matter.
                return [tiles]

            # Moves without salt.
            moves.extend(itertools.combinations(tiles, 2))  # May yield no items if numlegal == 1

            # Moves using salt.
            # Either this tile must have an odd count, or there must be more salts than 1+the number of odd elements.
            if salts_total > odd_cardinals+1 or odd:
                moves.extend(itertools.product(salts, tiles))

        # Finally, combinations of salt.
        if salts_legal - odd_cardinals >= 2:
            moves.extend(itertools.combinations(salts, 2))

        return moves


    def iter_run(self, steps=None):
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
                self.stack.append(SolverFrame(self, bitmap))
                continue
            if bitmap is None:  # Failed to make a move, so backtracking.
                if len(self.stack) > len(self.solution):  # Save new best attempt if it is one.
                    self.save_solution()
                self.stack.pop()
                if not self.stack:
                    return False  # Loss condition triggered.
                continue

            # Still here, so bitmap was zero meaning no tiles exist after doing this move.
            # WE WIN!
            self.save_solution()
            return True
        # Ran out of steps
        return None

#
#
#                 move = top.pop()
#                 _execute(move, False)
#                 bitmap &= ~Tile.bitmap(move)
#
#                     if (bitmap & move_bits) in self.bitmaps:
#                         # Discarded move.  Not an iteration
#                         self.bitmap_hits += 1
#                         continue
#                     bitmap &= move_bits
#                     self.bitmaps.add(bitmap)
#                     _execute(move, False)
#
#
#
#
#
#
#
#
#
# class GameState:
#     def __init__(self, board):
#         self.board = board
#         self.stack = None
#         self.solution = []
#         self.won = None
#         self.bitmaps = set()
#         self.bitmap_hits = 0
#         self.iterations = 0
#
#     def _checkwin(self):
#         self.won = all(not tiles for element, tiles in self.board.catalog) or None
#         return self.won
#
#     def run(self, steps=None):
#         if self.stack is None:
#             self.stack = [self.valid_moves()]
#             self.iterations = 0
#             self.bitmaps = set()
#             self.bitmap_hits = 0
#         top = self.stack[-1]
#
#         def _execute(tiles, exists):
#             for tile in tiles:
#                 tile.exists = exists
#
#         bitmap = self.board.bitmap()
#
#         while steps is None or steps > 0:
#             if steps > 0:
#                 steps -= 1
#             if top:  # Have a move available.
#                 move = top.pop()
#                 _execute(move, False)
#                 bitmap &= ~Tile.bitmap(move)
#
#                     if (bitmap & move_bits) in self.bitmaps:
#                         # Discarded move.  Not an iteration
#                         self.bitmap_hits += 1
#                         continue
#                     bitmap &= move_bits
#                     self.bitmaps.add(bitmap)
#                     _execute(move, False)
#
#
#
#
#
#                 exists = False
#             else:  # No moves available.
#
#
#
#         if self.stack is None:
#             self.stack = []
#         move = self.stack[-1].advance()
#         if move:
#             if self._checkwin():
#                 self.solution = list(frame.undo for frame in self.stack)
#                 return None
#             self.stack.append(MoveStackFrame(self))
#         if not move:
#             self.stack.pop()
#             if len(self.stack) > len(self.solution):
#                 self.solution = list(frame.undo for frame in self.stack)
#             if not self.stack:
#                 self.won = False
#                 return None
#             move = self.stack[-1].rewind()
#         return move
#
#     def simple_valid_moves(self):
#         """
#         Returns a list of valid moves.  Does not optimize.
#         """
#         moves = []
#
#         board = self.board
#
#         catalog = defaultdict(list)
#         for tile in board.legal_tiles():
#             element = tile.element
#             catalog[element].append(tile)
#
#         remaining_metals = board.remaining_metals()
#         if remaining_metals:
#             metal = remaining_metals[0]
#             if metal.legal:
#                 if catalog['quicksilver']:
#                     moves.extend(itertools.product(catalog['quicksilver'], [remaining_metals[0]]))
#                 elif len(remaining_metals) == 1:
#                     return [[metal]]
#
#         moves.extend(itertools.product(catalog['vitae'], catalog['mors']))
#         for cardinal in self.board.CARDINALS:
#             moves.extend(itertools.combinations(catalog[cardinal], 2))
#             moves.extend(itertools.product(catalog['salt'], catalog[cardinal]))
#         moves.extend(itertools.combinations(catalog['salt'], 2))
#         return moves
#
#
#
# class MoveStackFrame:
#     def __init__(self, gamestate):
#         self.gamestate = gamestate
#         self.undo = None
#         # self.moves = set(tuple(move) for move in self.gamestate.valid_moves())
#         # self.moves = self.gamestate.valid_moves()
#         self.moves = self.gamestate.valid_moves()
#         self.bitmap = None
#         # print(repr(self.moves))
#
#     def advance(self):
#         while True:
#             if not self.moves:
#                 return None
#             move = self.moves.pop()
#             self.undo = list((tile, tile.element) for tile in move)
#
#             # Execute move
#             for tile in move:
#                 tile.element = None
#
#             # Generate and check against bitmap
#             self.bitmap = self.gamestate.board.bitmap()
#             if self.bitmap in self.gamestate.bitmaps:
#                 self.rewind()
#                 continue
#             self.gamestate.bitmaps.add(self.bitmap)
#             return self.undo
#             #
#             #
#             #
#             #
#             # move = list((tile, None) for tile in move)
#             #
#             # # for frame in reversed(self.gamestate.stack[:-1]):
#             # #     if move in frame.moves:
#             # #         frame.moves.discard(move)
#             # #     else:
#             # #         break
#             #
#             # # for frame in self.gamestate.stack[:-2]:
#             # # if len(self.gamestate.stack) > 1:
#             # #     # If the parent has options A and B, it took A and we took B, there's no point in trying B on the parent
#             # #     # should we fail.
#             # #     self.gamestate.stack[-2].moves.discard(move)
#             # #
#             # #
#             # self.undo = list((tile, tile.element) for tile in move)
#             # move = list((tile, None) for tile in move)
#             # self._execute(move)
#
#     def rewind(self):
#         undo = self.undo
#         if not undo:
#             return None
#         self.undo = None
#         for tile, element in undo:
#             tile.element = element
#         return undo
#
vision = None

def click(where, down=0.05, up=0):
    x, y = where
    pyautogui.mouseDown(x=x, y=y)
    time.sleep(down)
    pyautogui.mouseUp(x=x, y=y)
    time.sleep(up)

def play_round():
    board = Board()



    print("Scanning board...")

    boardvision = BoardVision(vision)

    for tile in board.tiles:
        result = boardvision.match(tile)
        if result is None:
            tile.element = None
        else:
            tile.element = result.split('.')[0]
        tile.exists = result is not None

    for rownum, row in enumerate(board.rows[1:-1], start=1):
        results = []
        for tile in row[1:-1]:
            # if tile is None:
            #     element = '##########'
            # elif tile.element is None:
            #     element = ''
            # else:
            #     element = tile.element
            #     if tile.legal:
            #         element = element.upper()
            #
            # result = '{: ^10}'.format(element)
            results.append(f'{tile:%E}'.center(10))

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
        for num, move in enumerate(state.solution, start=1):
            print(f"#{num}: {move!r}")
        break
    else:
        print("Ran out of steps.")
        print("Current stack:")
        for num, frame in enumerate(state.stack, start=1):
            print(f"#{num}: {frame.undo!r}")
        print("Best run:")
        for num, move in enumerate(state.solution, start=1):
            print(f"#{num}: {move!r}")
        return state.won

    print("Elapsed time: ", time.time() - started)
    print("Bitmaps: ", len(state.bitmaps))
    print("Executing moveset in 5 seconds.   Ctrl+C to abort.")
    time.sleep(1)

    for num, move in enumerate(state.solution, start=1):
        print(f"#{num}: {move!r}")
        for part, (tile, element) in enumerate(move, start=1):
            x, y = map(int, tile.rect.middle)
            print(f"... {part}: ({x:4},{y:4}) Clicking on {element}")
            click(tile.rect.middle, down=0.01, up=0.05)

    time.sleep(2.5)
    return state.won


class Timer:
    def __init__(self, description='Timer'):
        self.elapsed = 0.0
        self.last = 0.0
        self.started = None
        self.description = description

    def start(self):
        self.started = time.time()

    def stop(self):
        self.last = time.time() - self.started
        self.elapsed += self.last
        self.started = None

    def reset(self):
        self.last = self.elapsed = 0
        self.started = None

    def __enter__(self):
        self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

class Program:
    def __init__(self):
        self.opts = None
        self.vision = None
        self.board = Board()
        self.executor = None
        self.parser = parser = argparse.ArgumentParser(description="Attempts to solve games of Sigmar's Garden.")
        self.timers = {
            'solve': Timer('Solve time'),
            'play': Timer('Execution'),
            'vision': Timer('Image Recognition'),
            'io': Timer('Image I/O')
        }
        self.timer_order = ('io', 'vision', 'solve', 'play')

        parser.add_argument('files', metavar='FILE', nargs='*', type=str,
                            help=(
                                'Simulate playing the specified screenshot images as individual games.'
                            )
                            )
        parser.add_argument('-d', '--data', action='store', default='./data',
                            help='Path to data directory')
        parser.add_argument('-p', '--play', action='store_true',
                            help='Actually play the game rather than merely outputting a solution.')
        parser.add_argument('-n', '--dry-run', action='store_true',
                            help='Display where clicks would happen, but don\'t actually click anything.'
                            )
        parser.add_argument(
            '-w', '--wait', action='store', type=float, default=2.0,
            help='Wait this long between determining a solution and executing it.'
        )
        parser.add_argument('-g', '--games', action='store', type=int, default=1,
                            help='Play this many games before stopping.  (0 = play forever)'
                            )
        parser.add_argument('-U', '--mouseup', action='store', type=float, default=0.25,
                            help='Time between clicks (in seconds)'
                            )
        parser.add_argument('-D', '--mousedown', action='store', type=float, default=0.01,
                            help='Time to hold mouse down when clicking'
                            )
        parser.add_argument('-M', '--movewait', action='store', type=float, default=0.00,
                            help='Additional delay between executing each move of a solution.'
                            )
        parser.add_argument('-H', '--hopeless', action='store_true',
                            help='Play out moves of a game even if it is determined to be unwinnable.'
                            )
        parser.add_argument('-P', '--processes', action='store', type=int, default=os.cpu_count() or 1,
                            help=(
                                'Use this many worker processes for image recognition.'
                                '  (0 = do image recognition in the main process)'
                            )
                            )
        parser.epilog = textwrap.dedent("""
            Currently, this only works on images and screenshots that are exactly 1920x1080.

            If a file is specified, the game will only be solved, not played, regardless of the other options.
            Specifying -p/--play is the same as -n/--dry-run in this case, and the setting for --games is ignored.
        """.strip())

    def show_timers(self):
        print('Last run: ')
        for key in self.timer_order:
            if key == 'play' and not self.opts.play:
                continue
            timer = self.timers[key]
            print(f"    {timer.description:20}: {timer.last:.3} sec.")

        print('Cumulative: ')
        for key in self.timer_order:
            if key == 'play' and not self.opts.play:
                continue
            timer = self.timers[key]
            print(f"    {timer.description:20}: {timer.elapsed:.3} sec.")

    def run(self, *args):
        opts = self.opts = self.parser.parse_args(*args)
        log.debug(repr(opts))
        self.datadir = Path(opts.data).resolve()
        log.info(f"Data directory is: {self.datadir}")
        baseline = self.datadir / "empty.png"
        self.vision = vision = Vision(self.datadir / "empty.png", extents=self.board.extents())
        log.info(f"Vision initialized with baseline of {baseline}, extents {vision.extents}")
        for file in self.datadir.glob('composite.*.png'):
            result = re.match(r'^composite\.(.*)\.png$', file.name.lower())
            if not result:
                continue
            tag = result.group(1)
            log.info(f"Adding composite {file.name} ('{tag}') to Vision ")
            vision.add_composite(tag, file)

        if opts.processes:
            self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=opts.processes)
            log.info(f'Process pool configured for {opts.processes} process(es)')
        else:
            self.executor = None
            log.info(f'Process pool not in use.')

        print(repr(opts.files))

        for path in opts.files:
            log.info(f'Reading {file}')
            path = Path(path).resolve()
            if path.is_dir():
                files = path.glob("*.png")
            else:
                files = [path]

            for f in files:
                log.info(f'Processing {f}')
                self.read_board(f)
                self.solve()

        if not opts.files:
            self.read_board(None)
            self.solve()
            self.show_timers()
            self.read_board(None)
            self.solve()

        self.show_timers()

    def solve(self):
        with self.timers['solve']:
            solver = Solver(self.board)
            result = solver.solve()

        status = "Puzzle solved" if result else "Failed to solve puzzle after"
        bitmaps = len(solver.bitmaps)
        print(f"Puzzle solved in {solver.iterations} iterations."
              f"  (Bitmaps used: {bitmaps}, hit: {solver.bitmap_hits})"
              )

        for num, move in enumerate(solver.solution, start=1):
            print(f"Move #{num}:")
            for tile in move:
                print(f"  - Tile #{tile:%n} ({tile:%e at %x, %y})")

    def read_board(self, image=None):
        with self.timers['io']:
            if image:
                self.vision.set_image(image)
            else:
                self.vision.screenshot()

        with self.timers['vision']:
            data = {}
            for tile in self.board.tiles:
                data[tile] = self.vision.match(tile, executor=self.executor)

            for tile, result in data.items():
                if self.executor:
                    result = result.result()

                if result is None:
                    tile.element = None
                else:
                    tile.element = result.split('.')[0]
                tile.exists = result is not None

        for ix, row in enumerate(self.board.rows[1:-1], start=1):
            output = []
            for tile in row[1:-1]:
                if tile.element is None:
                    text = ''
                else:
                    text = tile.element
                    if tile.legal:
                        text = text.upper()
                text = text[:6].center(6)
                output.append(text)
            padding = ' ' * (ix % 2 * 3)
            output = '|'.join(output)
            print(f"{ix:2}: {padding}{output}")

if __name__ == '__main__':
    Program().run()
    # board = Board()
    # print(repr(board.extents()))
    # initialize()
    # won = loss = timeout = 0
    # while True:
    #     result = play_round()
    #     if result:
    #         won += 1
    #     elif result is False:
    #         loss += 1
    #     else:
    #         timeout = 0
    #
    #     print(f"Won: {won} - Loss: {loss} - Timeout: {timeout}")
    #
    #     click((890, 890), up=0.25)
    #     time.sleep(5)
