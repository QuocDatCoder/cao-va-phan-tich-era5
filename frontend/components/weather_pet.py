"""Small vector weather mascot. States describe the assistant, never a forecast."""
from base64 import b64encode
from functools import lru_cache

PET_THEMES = {'auto':'Tự động', 'cloud':'Mây', 'sun':'Nắng', 'rain':'Mưa'}


def pet_mode(theme, *, pending=False, last_result=None):
    if theme in {'cloud','sun','rain'}:
        return theme
    if pending:
        return 'rain'
    if last_result is not None:
        return 'sun' if last_result.get('completed',last_result.get('verified')) else 'rain'
    return 'cloud'


def pet_svg(mode='cloud', *, unread=False, small=False):
    mode = mode if mode in {'cloud','sun','rain'} else 'cloud'
    sun = '''<g class="pet-sun"><g class="pet-rays" stroke="#f6bd4f" stroke-width="3" stroke-linecap="round">
        <path d="M85 9v-5m0 48v-5m23-19h5m-55 0h5m7-16-4-4m34 34 4 4m-4-34 4-4M70 42l-4 4"/>
        </g><circle cx="85" cy="28" r="15" fill="#ffda76"/></g>''' if mode == 'sun' else ''
    rain = '''<g class="pet-rain" fill="#68bce1">
        <path class="drop d1" d="M36 89q-10 13 0 14q10-1 0-14"/>
        <path class="drop d2" d="M59 95q-10 13 0 14q10-1 0-14"/>
        <path class="drop d3" d="M82 89q-10 13 0 14q10-1 0-14"/>
        </g>''' if mode == 'rain' else ''
    badge = '<circle cx="101" cy="38" r="7" fill="#0f766e" stroke="white" stroke-width="3"/>' if unread else ''
    prefix = 'mini' if small else 'launcher'
    return f'''<svg class="weather-pet" data-mode="{mode}" viewBox="0 0 120 120" aria-hidden="true">
      <defs><linearGradient id="pet-{prefix}" x1="0" y1="0" x2="0" y2="1">
        <stop stop-color="#fff"/><stop offset="1" stop-color="{'#d9eaf6' if mode=='rain' else '#e1f4ef'}"/>
      </linearGradient></defs>
      <ellipse class="pet-shadow" cx="59" cy="110" rx="30" ry="4" fill="#173d3b" opacity=".09"/>
      <g class="pet-float">{sun}
        <path d="M29 85C9 85 5 62 20 53c-1-16 15-26 29-20 7-19 35-15 39 6 19-1 30 17 20 32-4 9-12 14-23 14Z"
          fill="url(#pet-{prefix})" stroke="#79b8b1" stroke-width="2.2" stroke-linejoin="round"/>
        <path d="M24 53q5-5 12-3" fill="none" stroke="white" stroke-width="3.5" stroke-linecap="round"/>
        <g class="pet-eyes" fill="#23504e"><ellipse cx="44" cy="61" rx="3" ry="4"/>
          <ellipse cx="73" cy="61" rx="3" ry="4"/></g>
        <ellipse cx="34" cy="69" rx="6" ry="3" fill="#f5bdba" opacity=".7"/>
        <ellipse cx="83" cy="69" rx="6" ry="3" fill="#f5bdba" opacity=".7"/>
        <path d="M53 69q6 7 12 0" fill="none" stroke="#23504e" stroke-width="2.5" stroke-linecap="round"/>
        {rain}{badge}
      </g></svg>'''


PET_CSS = """
.weather-pet {display:block;width:100%;height:100%;overflow:visible;}
.pet-float {animation:pet-bob 4s ease-in-out infinite;transform-origin:60px 65px;}
.pet-eyes {animation:pet-blink 7s infinite;transform-origin:60px 61px;}
.pet-rays {animation:pet-sunturn 24s linear infinite;transform-origin:85px 28px;}
.pet-rain .drop {animation:pet-drizzle 1.5s ease-in infinite;}
.pet-rain .d2 {animation-delay:.5s;}.pet-rain .d3 {animation-delay:1s;}
@keyframes pet-bob {0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@keyframes pet-blink {0%,44%,48%,100%{transform:scaleY(1)}46%{transform:scaleY(.12)}}
@keyframes pet-sunturn {to{transform:rotate(360deg)}}
@keyframes pet-drizzle {0%{opacity:0;transform:translateY(-3px)}35%{opacity:.9}100%{opacity:0;transform:translateY(6px)}}
@media(prefers-reduced-motion:reduce) {.weather-pet * {animation:none!important;}}
"""


@lru_cache(maxsize=16)
def pet_art(mode='cloud', *, unread=False, small=False):
    # st.html's HTML sanitizer strips inline SVG. An SVG image remains inert and
    # preserves its own CSS animations without JavaScript or external assets.
    mode = mode if mode in {'cloud','sun','rain'} else 'cloud'
    svg=pet_svg(mode,unread=unread,small=small).replace('<defs>',f'<style>{PET_CSS}</style><defs>')
    svg=svg.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ',1)
    encoded=b64encode(svg.encode('utf-8')).decode('ascii')
    return f'<img class="weather-pet" data-mode="{mode}" src="data:image/svg+xml;base64,{encoded}" alt="" aria-hidden="true"/>'
