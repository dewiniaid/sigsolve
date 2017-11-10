import argparse
import logging
import re
import textwrap
import time
from pathlib import Path

import pyautogui

from sigsolve.board import Board
from sigsolve.solver import Solver
from sigsolve.util import click, Timer
from sigsolve.vision import Vision

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


class Program:
    SORTINDEX = {
        key: ix for ix, key in enumerate(
        ('air', 'earth', 'fire', 'water', 'salt',
         'mors', 'vitae',
         'quicksilver',
         'mercury', 'tin', 'iron', 'copper', 'silver', 'gold',
         'quintessence'
         )
        )
    }

    def __init__(self):
        self.opts = None
        self.vision = None
        self.board = Board()
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
        parser.add_argument('-F', '--fast', action='store_true',
                            help='Attempts ludicrous speed.'
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
        print(f"    {'Total time':20}: {sum(timer.last for timer in self.timers.values()):.3} sec.")

        print('Cumulative: ')
        for key in self.timer_order:
            if key == 'play' and not self.opts.play:
                continue
            timer = self.timers[key]
            print(f"    {timer.description:20}: {timer.elapsed:.3} sec.")
        print(f"    {'Total time':20}: {sum(timer.elapsed for timer in self.timers.values()):.3} sec.")

    def run(self, *args):
        opts = self.opts = self.parser.parse_args(*args)
        log.debug(repr(opts))
        if opts.fast:
            opts.mouseup = 0
            opts.mousedown = 0.0001
            opts.wait = 0.1


        self.datadir = Path(opts.data).resolve()
        log.info(f"Data directory is: {self.datadir}")
        baseline = self.datadir / "empty.png"
        self.vision = vision = Vision(self.datadir / "empty.png", bounds=self.board.bounds())
        log.info(f"Vision initialized with baseline of {baseline}, bounds {vision.bounds}")
        for file in self.datadir.glob('composite.*.png'):
            result = re.match(r'^composite\.(.*)\.png$', file.name.lower())
            if not result:
                continue
            tag = result.group(1)
            log.info(f"Adding composite {file.name} ('{tag}') to Vision ")
            vision.add_composite(tag, file)

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
                if not self.read_board(f):
                    log.error('Invalid boardstate!  Will try to solve it anyways.')
                solver = self.solve()
                if opts.dry_run:
                    self.play(solver)

        if not opts.play and not opts.dry_run:
            opts.games = 1

        if not opts.files:
            played = 0
            while played < opts.games or not opts.games:
                if played:
                    log.info('Beginning new game...')
                    # Click the New Game button
                    time.sleep(2)
                    click((890, 890))
                    time.sleep(5)
                played += 1
                log.info(f"===== GAME {played} of {opts.games or 'infinity'} =====")

                valid = self.read_board(None)
                if not valid:
                    log.error('Invalid boardstate!  Will try to solve it anyways.  Disabling playback.')
                    opts.dry_run = True
                    opts.games = 1
                solver = self.solve()

                if opts.play or opts.dry_run:
                    if not solver.won:
                        if self.opts.hopeless:
                            log.warning('Game is not winnable... but let\'s see how it plays out.')
                        else:
                            log.warning('Game is not winnable.')
                            self.show_timers()
                            continue
                    self.play(solver)
                self.show_timers()


    def board_is_valid(self):
        vitae = self.board.remaining('vitae')
        mors = self.board.remaining('mors')
        return not (
            # Invalidation conditions
            vitae > 4 or mors > 4 or vitae != mors
            or any(len(tiles) > 8 for element, tiles in self.board.catalog.items() if element in self.board.CARDINALS)
            or any(len(tiles) > 1 for element, tiles in self.board.catalog.items() if element in self.board.METALS)
            or self.board.remaining('quicksilver') > 5
        )

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

        return solver

    def play(self, solver):
        opts = self.opts
        time.sleep(opts.wait)

        with self.timers['play']:
            for num, move in enumerate(solver.solution, start=1):
                if num > 1:
                    time.sleep(opts.movewait)
                print(f"Move #{num}:")
                for tile in move:
                    pt = tile.sample_rect.middle
                    print(f"  - Tile #{tile:%n} ({tile:%e})  Clicking at ({pt.x}, {pt.y})")
                    if not opts.dry_run:
                        click(tile.sample_rect.middle, down=opts.mousedown, up=opts.mouseup)

    def read_board(self, image=None):
        with self.timers['io']:
            if image:
                self.vision.set_image(image)
            else:
                self.vision.screenshot()

        with self.timers['vision']:
            for tile in self.board.tiles:
                result = self.vision.match(tile)
                if result:
                    result = result.split('.', maxsplit=2)[0]
                tile.element = result
                tile.exists = result is not None

        print("\n".join(self.board.lines()))

        # for ix, row in enumerate(self.board.rows[1:-1], start=1):
        #     output = []
        #     for tile in row[1:-1]:
        #         if tile.element is None:
        #             text = ''
        #         else:
        #             text = tile.element
        #             if tile.legal:
        #                 text = text.upper()
        #         text = text[:6].center(6)
        #         output.append(text)
        #     padding = ' ' * (ix % 2 * 3)
        #     output = '|'.join(output)
        #     print(f"{ix:2}: {padding}{output}")
        #
        counts = [
            f"{element}={len(tiles)}" for element, tiles in sorted(
                self.board.catalog.items(),
                key=lambda x: self.SORTINDEX.get(x[0])
            )
        ]
        ok = self.board_is_valid()

        print(f"Element counts: {', '.join(counts)}  valid:{'OK' if ok else '*** not ok ***'}")
        return ok


if __name__ == '__main__':
    Program().run()
