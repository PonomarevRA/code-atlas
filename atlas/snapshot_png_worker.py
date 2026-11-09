"""Изолированный Pillow-рендерер: только JSON снимка и локальный шрифт."""

import colorsys
import json
import math
from pathlib import Path
import re
import sys
import textwrap

from PIL import Image, ImageColor, ImageDraw, ImageFont


def color(value):
    """Понимает CSS HSL проекта без загрузки внешних стилей."""
    value = str(value or '#416a60')
    if value.startswith('hsl'):
        numbers = [float(item) for item in re.findall(r'[\d.]+', value)]
        if len(numbers) >= 3:
            return tuple(round(channel * 255) for channel in colorsys.hls_to_rgb(numbers[0] / 360, numbers[2] / 100, numbers[1] / 100))
    try:
        return ImageColor.getrgb(value)
    except ValueError:
        return (65, 106, 96)


def route(left, right):
    """Повторяет SVG: боковые порты и две опоры на 1/3 и 2/3 свободного пролёта."""
    x1, y1 = left['x'] + left['width'], left['y'] + left['height'] / 2
    x2, y2 = right['x'], right['y'] + right['height'] / 2
    span = x2 - x1
    controls = [(x1,y1), (x1+span/3,y1), (x1+2*span/3,y2), (x2,y2)]
    if left['y'] == right['y'] and abs(right['x']-left['x']) > left['width']+60:
        a = (left['x']+left['width']/2, left['y']+left['height'])
        b = (right['x']+right['width']/2, right['y']+right['height'])
        controls = [a, (a[0],a[1]+16), (b[0],b[1]+16), b]
    points = []
    for step in range(33):
        t = step / 32
        u = 1 - t
        points.append(tuple(u**3*controls[0][axis] + 3*u*u*t*controls[1][axis] + 3*u*t*t*controls[2][axis] + t**3*controls[3][axis] for axis in (0,1)))
    return points


def render(view, output, font_path):
    """Рисует проектные блоки, исходные связи и отдельные пояснения в закреплённых координатах."""
    layout = view['layout']
    image = Image.new('RGB', (int(layout['width']), int(layout['height'])), '#f5f8f5')
    draw = ImageDraw.Draw(image)
    fonts = {size: ImageFont.truetype(font_path, size) for size in (11, 13, 14, 16, 20)}
    draw.text((20, 16), str(view.get('title', 'Представление'))[:150], font=fonts[20], fill='#203a34')
    draw.text((20, 46), str(view.get('query', ''))[:160], font=fonts[13], fill='#586c64')
    draw.text((20, 70), 'Сплошные связи — код; пунктир и жёлтые заметки — пояснения агента.', font=fonts[11], fill='#85632d')
    positions = layout['nodes']
    for project in layout['projects']:
        x, y, w, h = (project[key] for key in ('x', 'y', 'width', 'height'))
        draw.rounded_rectangle((x, y, x+w, y+h), radius=14, fill=color(project.get('color', {}).get('fill', '#eaf0ed')), outline=color(project.get('color', {}).get('stroke')), width=2)
        label = str(project['label'])
        maximum = max(1, int((w-32)/9))
        label = label if len(label) <= maximum else label[:maximum-1]+'…'
        draw.text((x+12, y+10), label, font=fonts[16], fill=color(project.get('color', {}).get('text', '#203a34')))
    for edge in view['edges']:
        if edge['source'] not in positions or edge['target'] not in positions:
            continue
        points = route(positions[edge['source']], positions[edge['target']])
        draw.line(points, fill='#78978b', width=2)
        x, y = points[-1]
        dx, dy = x-points[-2][0], y-points[-2][1]
        length = math.hypot(dx,dy) or 1
        dx, dy = dx/length, dy/length
        draw.polygon([(x,y),(x-7*dx-4*dy,y-7*dy+4*dx),(x-7*dx+4*dy,y-7*dy-4*dx)], fill='#78978b')
    notes = layout.get('annotations', {})
    for edge in view.get('annotationEdges', []):
        left, right = notes.get(edge['source']), positions.get(edge['target'])
        if not left or not right:
            continue
        a = (left['x']+left['width']/2, left['y']+left['height']/2)
        b = (right['x']+right['width']/2, right['y']+right['height']/2)
        for step in range(0, 40, 2):
            start = tuple(a[i]+(b[i]-a[i])*step/40 for i in (0,1))
            end = tuple(a[i]+(b[i]-a[i])*(step+1)/40 for i in (0,1))
            draw.line([start,end], fill='#ad8848', width=2)
    for node in view['nodes']:
        box = positions[node['id']]
        x, y, w, h = (box[key] for key in ('x','y','width','height'))
        draw.rounded_rectangle((x,y,x+w,y+h), radius=7, fill='#ffffff', outline=color(node.get('color')), width=2)
        lines = box.get('displayLines') or textwrap.wrap(str(node.get('label',node['id'])),26)[:3]
        for index, line in enumerate(lines[:3]):
            draw.text((x+12,y+9+index*16),line,font=fonts[14],fill='#203a34')
        draw.text((x+12,y+h-18),str(node.get('kind','symbol'))[:30],font=fonts[11],fill='#667a70')
    for note in view.get('annotations', []):
        box = notes[note['id']]
        x, y, w, h = (box[key] for key in ('x','y','width','height'))
        draw.rounded_rectangle((x,y,x+w,y+h),radius=8,fill='#fff5dc',outline='#ad8848',width=2)
        cursor = y+10
        for line in box.get('titleLines', [note['title']]):
            draw.text((x+12,cursor),line,font=fonts[14],fill='#40351d')
            cursor += 16
        cursor += 16
        for line in box.get('displayLines', textwrap.wrap(note['text'],40)):
            if cursor+16 > y+h-12:
                break
            draw.text((x+12,cursor),line,font=fonts[13],fill='#40351d')
            cursor += 16
        if box.get('textTruncated'):
            draw.text((x+12,y+h-18),'… Полный текст в снимке',font=fonts[11],fill='#85632d')
    image.save(output, format='PNG')


if __name__ == '__main__':
    render(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2], sys.argv[3])
