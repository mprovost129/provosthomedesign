"""Backwards-compatible, bounded annotation payloads in normalized page space."""
import math
from django.core.exceptions import ValidationError

def validate_shape(mark):
    base = {'id', 'page', 'x', 'y', 'text'}
    kind = mark.get('type', 'pin')
    if kind == 'pin':
        if set(mark) not in (base, base | {'type'}):
            raise ValidationError('Invalid note fields.')
        return 0
    if kind not in {'arrow', 'box', 'freehand'} or set(mark) != base | {'type', 'points'}:
        raise ValidationError('Choose a note, arrow, box, or freehand mark.')
    points = mark['points']
    if not isinstance(points, list) or not 2 <= len(points) <= (300 if kind == 'freehand' else 2):
        raise ValidationError('A drawing needs two endpoints, or up to 300 freehand points.')
    for point in points:
        if (not isinstance(point, list) or len(point) != 2 or any(type(value) not in (int, float)
                or not math.isfinite(value) or not 0 <= value <= 1 for value in point)):
            raise ValidationError('Drawing points must stay on the plan page.')
    if points[0] != [mark['x'], mark['y']]:
        raise ValidationError('The drawing location must match its first point.')
    if max(abs(point[0]-points[0][0]) + abs(point[1]-points[0][1]) for point in points) < .001:
        raise ValidationError('Draw a longer mark, or use a note pin.')
    return len(points)
