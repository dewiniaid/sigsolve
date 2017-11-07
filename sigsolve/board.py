import collections
import itertools
import re

from sigsolve.geometry import DEFAULT_GEOMETRY, Point, Rect


class TileBase:
    """Base class for tiles."""
    def __init__(self, parent=None, number=None):
        self.parent = parent
        self._exists = False
        self.number = number
        self.bit = 0 if number is None else 1 << number
        self.neighbors = []
        self._element = None
        self._legal = False

    @property
    def legal(self):
        return self._legal

    def real_neighbors(self):
        yield from (n for n in self.neighbors if n.element)

    def nonempty_neighbors(self):
        yield from (n for n in self.neighbors if n.exists)

    @classmethod
    def bitmap(cls, tiles):
        result = 0
        for tile in tiles:
            result |= tile.bit
        return result

    def _format_dict(self, *bases):
        result = {
            'n': (self.number is None and '?') or self.number,
            'b': self.bit,
            'e': self.element or 'none',
        }
        if self.element is None or not self.exists:
            result['E'] = 'empty'
        elif self._legal:
            result['E'] = self.element.upper()
        else:
            result['E'] = self.element
            if self._legal is None:
                result['E'] += '?'

        for base in bases:
            if base:
                result.update(base)
        return result

    def __format__(self, format_spec):
        """Allows tiles to be formatted pretty in F-strings and str.format()"""
        d = self._format_dict()
        return re.sub('%.', lambda match: str(d.get(match.group(0)[1], '')), format_spec)

    @property
    def exists(self):
        return self._exists

    @exists.setter
    def exists(self, value):
        self._setexists(value)

    def _setexists(self, value):
        old = self._exists
        if value == old:
            return  # noop
        if value and self.element is None:
            raise AttributeError('Cannot make a tile with no element existant')
        self._exists = value
        if self.parent:
            self.parent.tile_exists_changed(self, old, value)

    @property
    def element(self):
        return self._element

    @element.setter
    def element(self, value):
        self._setelement(value)

    def _setelement(self, value):
        old = self._element
        if value == old:
            return
        self._element = value
        if self.parent:
            self.parent.tile_element_changed(self, old, value)
        if not value:
            self.exists = False


class Tile(TileBase):
    MINADJACENT = 3  # Number of adjacent empty tiles that must be present for a move to be legal.

    def __init__(self, *xy, geometry=DEFAULT_GEOMETRY, parent=None, number=None):
        super().__init__(parent, number)

        self._legal = None
        self.geometry = geometry
        self.xy = Point(*xy)
        self.origin = (geometry.full_size * self.xy) + geometry.origin
        if self.xy.y % 2:
            self.origin += geometry.altoffset
        self.rect = Rect(self.origin, self.origin + geometry.size)
        self.sample_rect = self.rect + geometry.sample_insets

    @property
    def x(self):
        return self.xy.x

    @property
    def y(self):
        return self.xy.y

    @property
    def legal(self):
        return self.exists and self.element is not None and self.predict_legality()

    def expire_legality(self, onlyif=None):
        """Forgets current legality status, causing it to be updated on next request."""
        if onlyif is not None and self._legal is not onlyif:
            return
        self._legal = None

    def predict_legality(self, removed=None):
        """
        Calculates legality status, assuming tiles in `removed` are removed.

        If self._legal is already True, returns True immediately (since removing additional tiles will have no effect)

        If `ignore` is None or has no impact on legality, the current cached legality status will be updated.
        Reasons legality may not be affected include:
            - The tile is illegal anyways.
            - None of the tiles in 'ignore' are adjacent, or they all are already empty.
            - Adjacency criteria are met even without the tiles in `ignore` being considered.

        :param removed: Set of tiles to ignore.  None = ignore no tiles.
        :return: True if this tile is legal, False otherwise.
        """
        if not self.exists or self.element is None:
            return False

        if self._legal or (not removed and self._legal is False):
            return self._legal
        if removed is None:
            removed = set()

        def _gen():
            # Iterate over all neighbors.  Then iterate over the first N results to handle wrapping around.
            cache = []
            cache_count = self.MINADJACENT - 1
            for neighbor in self.neighbors:
                legality_predicted = (not neighbor.exists) or neighbor in removed
                if not legality_predicted:
                    cache_count = 0  # Stop cacheing (the 'False' results don't need to be repeated)
                result = (not neighbor.exists, legality_predicted)
                if cache_count:
                    cache.append(result)
                    cache_count -= 1
                yield result
            yield from cache

        result = False     # What we'll return at the end if we don't bail early.
        actual_run = 0         # Actual run of legal tiles
        predicted_run = 0      # Predicted run of legal tiles, counting `removed`

        for actual, predicted in _gen():
            if actual:
                actual_run += 1
                if actual_run >= self.MINADJACENT:
                    self._legal = True
                    return True
            else:
                actual_run = 0

            if predicted:
                predicted_run += 1
                if predicted_run >= self.MINADJACENT:
                    result = True
            else:
                predicted_run = 0

        # If we reach here, it's not ACTUALLY legal so update status accordingly.
        self._legal = False
        # But it might be predicted legal...
        return result

    def affected_neighbors(self):
        """Returns a list of neighbors that would become legal if this tile is removed."""
        ignore = {self}
        result = []
        for neighbor in self.nonempty_neighbors():
            if neighbor.predict_legality(removed=ignore):
                if neighbor.legal:
                    continue
                result.append(neighbor)
        return result

    @classmethod
    def all_neighbors(cls, tiles):
        """
        Returns the set of all neighbors of `tiles`.
        :param tiles: Tiles to check
        :return: All neighbors, excluding tiles in `tiles`
        """
        neighbors = set()
        for tile in tiles:
            if tile is None:
                continue
            neighbors.update(tile.real_neighbors())

        neighbors.discard(None)
        neighbors.difference_update(tiles)
        return neighbors

    @classmethod
    def affected_tiles(cls, tiles):
        """Returns a set of tiles that will become legal if all tiles in `tiles` are removed."""
        affected = set()
        for tile in cls.all_neighbors(tile for tile in tiles if tile.exists):
            if tile.element is None:
                continue
            if tile.predict_legality(tiles) and not tile.legal:  # Order matters!
                affected.add(tile)
        return affected

    def __repr__(self):
        status = (self.exists and self.element) or 'empty'
        if self.exists:
            if self._legal:
                status = status.upper()
            elif self._legal is None:
                status += '?'
        return f"{self.__class__.__name__}({self.x}, {self.y})  {status}"

    def _format_dict(self, *bases):
        return super()._format_dict({
            'x': self.x,
            'y': self.y
        })


class DummyTile(TileBase):
    def __init__(self, parent=None):
        super().__init__(parent)

    def _setexists(self, value):
        raise AttributeError('DummyTile instances can never exist.')

    def _setelement(self, value):
        raise AttributeError('DummyTile instances can never have an element.')


class CatalogDictionary(collections.defaultdict):
    """
    We don't want accesses to missing key to actually add data to the dictionary, so they just return a dummy value.
    """
    def __missing__(self, key):
        return tuple()


class Board:
    CARDINALS = {'water', 'earth', 'fire', 'air'}
    METALS = ('mercury', 'tin', 'iron', 'copper', 'silver', 'gold')

    def __init__(self, geometry=DEFAULT_GEOMETRY):
        diameter = 2*geometry.radius - 1

        self.rows = []
        self.tiles = []
        self.dummy = DummyTile(parent=self)
        self.catalog = CatalogDictionary()

        # Pad with a row of empties for easier neighbor calculations later.
        blank_row = list(itertools.repeat(self.dummy, diameter + 2))
        self.rows.append(blank_row)

        hoffset = (geometry.radius - 1) // 2  # Used for mapping screenspace coordinates to boardspace
        number = 0

        for y in range(0, diameter):
            row = list(blank_row)
            self.rows.append(row)
            count = diameter - abs(geometry.radius - (y+1))
            start = (diameter - count) // 2
            for x in range(start, start+count):
                t = Tile(x-hoffset, y, parent=self, number=number)
                number += 1
                self.tiles.append(t)
                row[x+1] = t

        # End padding, too.
        self.rows.append(blank_row)

        # Calculate adjacency data
        for y, row in enumerate(self.rows):
            altrow = -((y+1)%2)
            if y == 0 or y > diameter:
                continue
            above = self.rows[y-1]
            below = self.rows[y+1]
            for x, tile in enumerate(row):
                if tile is self.dummy:  # Dummy tiles don't need neighbors.
                    continue

                # Starting from the left and going clockwise
                tile.neighbors = [
                    row[x-1],  # Left
                    above[x+altrow],  # Upper left
                    above[x+altrow+1],  # Upper right
                    row[x+1],  # Right
                    below[x+altrow+1],  # Lower right
                    below[x+altrow],  # Lower left
                ]

    def tile_element_changed(self, tile, old, new):
        """Called when a child tile's element is changed.  Used to update the catalog and legality data."""
        if old == new:
            return  # Nothing changed.
        if old is not None:
            self.catalog[old].discard(tile)
        if new is not None and tile.exists:
            self.catalog.setdefault(new, set()).add(tile)

    def tile_exists_changed(self, tile, old, new):
        if old == new:
            return  # No element change, thus no legality changes.
        if tile.element:
            if new:
                self.catalog.setdefault(tile.element, set()).add(tile)
            elif tile.element in self.catalog:
                self.catalog[tile.element].discard(tile)
        for neighbor in tile.real_neighbors():
            # If we're gaining an element, expire anything that was previously legal.
            # If we're losing an element, expire anything that was previously not legal.
            neighbor.expire_legality(new)

    def legal_tiles(self):
        """Yields a list of tiles that are legal."""
        return [t for t in self.tiles if t.legal]

    def remaining_cardinals(self):
        return {e: self.remaining(e) for e in self.CARDINALS}

    def remaining_metals(self):
        return list(list(self.catalog[e])[0] for e in self.METALS if self.catalog[e])

    def remaining(self, element):
        return len(self.catalog[element])

    def bitmap(self):
        """
        Returns an integer representing which tiles are empty.
        """
        return TileBase.bitmap(tile for tile in self.tiles if tile.exists)

    def extents(self):
        """
        Returns a Rect corresponding to the entire screenspcae area needed by this board
        :return:
        """
        xmin, ymin, xmax, ymax = self.tiles[0].rect.coords  # Arbitrary initialization

        for tile in self.tiles:
            for rect in tile.rect, tile.sample_rect:
                xmin = min(xmin, rect.left)
                xmax = max(xmax, rect.right)
                ymin = min(ymin, rect.top)
                ymax = max(ymax, rect.bottom)
        return Rect(xmin, ymin, xmax, ymax)
