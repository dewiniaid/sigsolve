"""Geometry utility classes."""
from collections import namedtuple
from collections.abc import Iterable


__all__ = ['Point', 'Rect', 'Geometry', 'DEFAULT_GEOMETRY', 'RELATIVE_GEOMETRY']


class Point(namedtuple('Point', 'x y')):
    ORIGIN = None
    def __new__(cls, *args, **kwargs):
        if isinstance(args[0], Point):
            return args[0]
        if isinstance(args[0], tuple):
            args = args[0]
            print(repr(args))

            # return cls.__new__(*args)
        return super().__new__(cls, *args, **kwargs)

    def __add__(self, other):
        if isinstance(other, Rect):
            return other + self
        other = Point(other)
        if not self:
            return other
        if not other:
            return self
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        if isinstance(other, Rect):
            return other + self
        other = Point(other)
        if not self:
            return other
        if not other:
            return self
        return Point(self.x - other.x, self.y - other.y)

    def scale(self, other):
        if isinstance(other, tuple):
            if other[0] == 1 and other[1] == 1:
                return self
            return Point(self.x * other[0], self.y * other[1])
        if other == 1:
            return self
        return Point(self.x * other, self.y * other)

    __mul__ = scale
    # def __mul__(self, other):
    #     return
    #     if isinstance(other, tuple):
    #         if other[0] == 1 and other[1] == 1:
    #             return self
    #         return Point(self.x * other[0], self.y * other[1])
    #     if other == 1:
    #         return self
    #     return Point(self.x * other, self.y * other)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __neg__(self):
        return self.scale(-1)

    @property
    def width(self):  # alias for self.x, useful for when referring to a size.
        return self[0]

    @property
    def height(self):  # alias for self.y, useful for when referring to a size.
        return self[1]

    def __bool__(self):
        return self[0] != 0 or self[1] != 0

Point.ORIGIN = Point(0, 0)


class Rect(tuple):
    def __new__(cls, x1=0, y1=0, x2=None, y2=None, w=None, h=None, size=None):
        if isinstance(x1, tuple):
            # Assume two argument version with 2 points.
            return super().__new__(cls, (Point(*x1), Point(*y1)))

        if size is not None:
            w, h = size
        if w is not None:
            x2 = x1 + w
        if h is not None:
            y2 = y1 + h
        if x2 is None:
            x2 = x1
        if y2 is None:
            y2 = y1

        xy1 = Point(x1, y1)
        xy2 = Point(x2, y2)
        return super().__new__(cls, (xy1, xy2))

    @classmethod
    def bounds(cls, rects):
        if not rects:
            return None

        def _minmax(result, *args):
            if not result:
                return [min(*args), max(*args)]
            else:
                result[0] = min(result[0], *args)
                result[1] = max(result[1], *args)
            return result
        xbounds = ybounds = None
        for rect in rects:
            xbounds = _minmax(xbounds, rect[0][0], rect[1][0])
            ybounds = _minmax(ybounds, rect[0][1], rect[1][1])

        return Rect(*zip(xbounds, ybounds))

    @property
    def xy1(self):
        return self[0]

    @property
    def xy2(self):
        return self[1]

    @property
    def x1(self):
        return self.xy1[0]

    @property
    def x2(self):
        return self.xy2[0]

    @property
    def y1(self):
        return self.xy1[1]

    @property
    def y2(self):
        return self.xy2[1]

    @property
    def coords(self):
        # Returns a flattened tuple (x1,y1,x2,y2)
        return self.x1, self.y1, self.x2, self.y2

    @property
    def width(self):
        return abs(self[1][0] - self[0][0])

    @property
    def height(self):
        return abs(self[1][1] - self[0][1])

    @property
    def size(self):
        return Point(self.width, self.height)

    @property
    def left(self):
        return min(self[0][0], self[1][0])

    @property
    def right(self):
        return max(self[0][0], self[1][0])

    @property
    def top(self):
        return min(self[0][1], self[1][1])

    @property
    def bottom(self):
        return max(self[0][1], self[1][1])

    @property
    def middle(self):
        return Point((self[0][0] + self[1][0]) / 2, (self[0][1] + self[1][1]) / 2)

    def __bool__(self):
        return True if self[0] or self[1] else False

    def __add__(self, other):
        if not other:
            return self
        if isinstance(other, Rect):
            return Rect(self[0] + other[0], self[1] + other[1])
        if isinstance(other, Iterable):
            xy1 = self[0] + other
            xy2 = self[1] + other

        return Rect(
            self[0] + other, self[1] + other
        )

    def __sub__(self, other):
        if not other:
            return self
        if isinstance(other, Rect):
            return Rect(self[0] - other[0], self[1] - other[1])
        if isinstance(other, Iterable):
            xy1 = self[0] - other
            xy2 = self[1] - other

        return Rect(
            self[0] - other, self[1] - other
        )

    def __mul__(self, other):
        if isinstance(other, Iterable):
            other = Point(other)
            if other.x == 1 and other.y == 1:
                return self
            xy1 = self[0] * other
            xy2 = self[1] * other
            return Rect(xy1, xy2)
        if other == 1:
            return self
        return Rect(
            self[0] * other, self[1] * other
        )

    def __getnewargs__(self):
        return tuple(self)


class Geometry:
    _NORMAL_SYMBOLS = 'salt . air fire water earth | quicksilver .'.split(' ')
    _METAL_SYMBOLS = 'mercury tin iron copper silver gold'.split(' ')
    def __init__(
            self,
            radius=6,  # Tiles to a side
            origin=Point(1020, 192),  # Coords of leftmost tile in top row
            borders=Point(2, 1),  # Border width
            size=Point(64, 56),  # Tile size
            sample_insets=Rect(16, 8, -16, -8),  # Subtracted from tile size to determine region to sample pixels from
            altoffset=None,  # Offsets for alternating row.  Automatically calculated if None.
            legend_origin=Point(953, 868),  # Upper-left of first symbol in the legend.
            legend_symbol_size=Rect(0, 0, 34, 34),  # How big one symbol in the legend area is.
            legend_gap_offset=Point(8, 0),  # Offset by this much between symbols.
            legend_metal_offset=Point(6, 0),  # Offset by this much between symbols when we reach metal
            legend_dot_offset=Point(4, 0),  # How wide a dot is
            legend_bar_offset=Point(10, 0),  # How wide the bar is.  (This width factors in the extra spacing)
            new_game=Point(890, 890)  # Location of the new game button.
     ):
        self.radius = radius
        self.origin = origin
        self.borders = borders
        self.size = size
        self.sample_insets = sample_insets
        self.full_size = self.size + self.borders
        self.new_game = new_game

        if altoffset is None:
            altoffset = Point(-self.full_size.width // 2, 0)
        self.altoffset = altoffset

        self.legend_origin = legend_origin
        if legend_origin is not None:
            legend_dot_offset += legend_gap_offset
            legend_bar_offset += legend_gap_offset
            legend_symbol_offset = legend_gap_offset + Point(legend_symbol_size.width, 0)
            legend_metal_offset = legend_metal_offset + Point(legend_symbol_size.width, 0)
            rect = legend_origin + legend_symbol_size
            self.legend = {}
            for sym in self._NORMAL_SYMBOLS:
                if sym == '.':
                    rect += legend_dot_offset
                    continue
                if sym == '|':
                    rect += legend_bar_offset
                    continue
                self.legend[sym] = rect
                rect += legend_symbol_offset
            for sym in self._METAL_SYMBOLS:
                self.legend[sym] = rect
                rect += legend_metal_offset
            self.legend['quintessence'] = self.legend['earth'] + legend_dot_offset + legend_symbol_offset

    def from_point(self, point):
        result = type(self)(
            radius=self.radius, origin=self.origin - point, size=self.size, sample_insets=self.sample_insets,
            altoffset=self.altoffset, legend_origin=None
        )
        result.legend_origin = self.legend_origin - point
        result.legend = {k: v - point for k, v in self.legend.items()}
        result.new_game = self.new_game - point
        return result

    def from_origin(self):
        """Creates a copy of this geometry but with an origin of 0."""
        return self.from_point(self.origin)


DEFAULT_GEOMETRY = Geometry()
RELATIVE_GEOMETRY = DEFAULT_GEOMETRY.from_origin()
