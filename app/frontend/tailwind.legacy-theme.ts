/**
 * Theme bridge for the legacy screens (src/components/*.js) until they are rebuilt in React (Phase 3).
 *
 * The legacy screens were designed dark-first with raw palette classes (bg-slate-900, text-white,
 * text-indigo-300, border-slate-800 ...). Rewriting their markup is out of scope, so the palette
 * entries they use are routed through CSS variables whose fallbacks are the stock Tailwind colors:
 *
 *   .text-slate-300 { color: rgb(var(--ltx-slate-300, 203 213 225) / var(--tw-text-opacity)) }
 *
 * Outside `.legacy-scope` nothing defines these variables, so every class renders exactly like stock
 * Tailwind (the React shell uses semantic token classes anyway). Inside `.legacy-scope` the plugin
 * below redefines them:
 *   - light theme: the slate scale is inverted (dark surfaces become light surfaces, light text
 *     becomes dark text) and colored text moves to the lightest shade that still passes WCAG AA
 *     (4.5:1) on white, slate-50/100 and its own pastel tints;
 *   - dark theme: body text that failed AA on slate-900 (slate-500/600) is lifted to slate-400;
 *   - both themes: solid button colors that failed AA with white text (emerald-600, amber-600) are
 *     darkened one step.
 * Elements on solid accent backgrounds (buttons) keep their designed text colors in light mode,
 * because the variables are reset to their fallbacks there.
 */
import colors from 'tailwindcss/colors';
import plugin from 'tailwindcss/plugin';

type Family =
  | 'slate'
  | 'indigo'
  | 'violet'
  | 'purple'
  | 'blue'
  | 'sky'
  | 'teal'
  | 'emerald'
  | 'amber'
  | 'orange'
  | 'rose';
type Shade = 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 950;
type Usage = Partial<Record<Family, Shade[]>>;

/** Palette entries the legacy screens use, per utility type (inventoried from src/components). */
const TEXT: Usage = {
  slate: [100, 200, 300, 400, 500, 600],
  indigo: [200, 300, 400, 600],
  violet: [300, 400],
  purple: [300],
  blue: [300],
  sky: [300, 400],
  teal: [300],
  emerald: [100, 300, 400, 500, 600],
  amber: [100, 200, 300, 400],
  orange: [300],
  rose: [100, 300, 400],
};
const BACKGROUND: Usage = {
  slate: [700, 800, 900, 950],
  indigo: [900, 950],
  violet: [900, 950],
  sky: [950],
  emerald: [600, 950],
  amber: [600, 900, 950],
  rose: [800, 900, 950],
};
const BORDER: Usage = {
  slate: [600, 700, 800, 900],
  indigo: [800],
  violet: [800],
  sky: [700],
  emerald: [700, 800],
  rose: [800, 900],
};
const GRADIENT: Usage = {
  slate: [800, 900, 950],
  indigo: [950],
  amber: [900, 950],
};
const PLACEHOLDER: Usage = { slate: [500] };

/** Darkest-needed shade for colored text on light surfaces (computed: >= 4.6:1 on white, slate-100, own tints). */
const LIGHT_TEXT_SHADE: Record<Family, Shade> = {
  slate: 600,
  indigo: 700,
  violet: 700,
  purple: 700,
  blue: 700,
  sky: 800,
  teal: 800,
  emerald: 800,
  amber: 800,
  orange: 800,
  rose: 800,
};

const SLATE_TEXT_LIGHT: Partial<Record<Shade, Shade>> = { 100: 900, 200: 800, 300: 700, 400: 600, 500: 600, 600: 600 };
const SLATE_SURFACE_LIGHT: Partial<Record<Shade, Shade | 'white'>> = { 700: 200, 800: 100, 900: 'white', 950: 50 };
const SLATE_BORDER_LIGHT: Partial<Record<Shade, Shade>> = { 600: 300, 700: 300, 800: 200, 900: 200 };

const hexToChannels = (hex: string): string => {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? value.replace(/^(.)(.)(.)$/, '$1$1$2$2$3$3') : value;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)).join(' ');
};
const tw = (family: Family | 'white', shade?: Shade): string =>
  family === 'white' || shade === undefined ? '#ffffff' : colors[family][shade];
const channels = (family: Family | 'white', shade?: Shade): string => hexToChannels(tw(family, shade));

const varName = (prefix: string, family: Family | 'white', shade?: Shade): string =>
  shade === undefined ? `--${prefix}-${family}` : `--${prefix}-${family}-${String(shade)}`;

type ColorTree = Record<string, string | Record<string, string>>;

/** Builds `{ slate: { 300: 'rgb(var(--ltx-slate-300, 203 213 225) / <alpha-value>)' } }`. */
function variableColors(prefix: string, usage: Usage, withWhite = false): ColorTree {
  const tree: ColorTree = {};
  for (const [family, shades] of Object.entries(usage) as [Family, Shade[]][]) {
    const entries: Record<string, string> = {};
    for (const shade of shades) {
      entries[String(shade)] = `rgb(var(${varName(prefix, family, shade)}, ${channels(family, shade)}) / <alpha-value>)`;
    }
    tree[family] = entries;
  }
  if (withWhite) {
    tree.white = `rgb(var(${varName(prefix, 'white')}, 255 255 255) / <alpha-value>)`;
  }
  return tree;
}

/** Theme `extend` entries that route the legacy palette through the variables. */
export const legacyThemeExtend = {
  textColor: variableColors('ltx', TEXT, true),
  backgroundColor: variableColors('lbg', BACKGROUND),
  borderColor: variableColors('lbd', BORDER, true),
  gradientColorStops: variableColors('lgs', GRADIENT),
  placeholderColor: variableColors('lph', PLACEHOLDER),
};

type Declarations = Record<string, string>;

function lightDeclarations(): Declarations {
  const out: Declarations = {};
  out[varName('ltx', 'white')] = channels('slate', 900);
  for (const [family, shades] of Object.entries(TEXT) as [Family, Shade[]][]) {
    for (const shade of shades) {
      let target: Shade;
      if (family === 'slate') target = SLATE_TEXT_LIGHT[shade] ?? 600;
      else target = shade <= 200 ? 900 : LIGHT_TEXT_SHADE[family];
      out[varName('ltx', family, shade)] = channels(family, target);
    }
  }
  const surface = (prefix: string, usage: Usage) => {
    for (const [family, shades] of Object.entries(usage) as [Family, Shade[]][]) {
      for (const shade of shades) {
        if (family === 'slate') {
          const target = SLATE_SURFACE_LIGHT[shade];
          if (target === undefined) continue;
          out[varName(prefix, family, shade)] = target === 'white' ? channels('white') : channels('slate', target);
        } else if (shade >= 800) {
          // Dark tints (bg-indigo-950/60, from-amber-950/40 ...) become pastel tints of the same hue.
          out[varName(prefix, family, shade)] = channels(family, shade >= 950 ? 50 : 100);
        }
      }
    }
  };
  surface('lbg', BACKGROUND);
  surface('lgs', GRADIENT);
  out[varName('lbd', 'white')] = channels('slate', 900); // border-white/5 -> a faint dark hairline
  for (const [family, shades] of Object.entries(BORDER) as [Family, Shade[]][]) {
    for (const shade of shades) {
      if (family === 'slate') {
        const target = SLATE_BORDER_LIGHT[shade];
        if (target !== undefined) out[varName('lbd', family, shade)] = channels('slate', target);
      } else {
        out[varName('lbd', family, shade)] = channels(family, shade >= 800 ? 200 : 300);
      }
    }
  }
  return out;
}

/** In light mode, solid accent buttons keep their designed text colors (white on indigo-600 ...). */
function resetTextDeclarations(): Declarations {
  const out: Declarations = { [varName('ltx', 'white')]: 'initial' };
  for (const [family, shades] of Object.entries(TEXT) as [Family, Shade[]][]) {
    for (const shade of shades) out[varName('ltx', family, shade)] = 'initial';
  }
  return out;
}

const SOLID_BACKGROUNDS = [
  '.bg-indigo-600',
  '.bg-indigo-700',
  '.bg-violet-600',
  '.bg-violet-700',
  '.bg-emerald-600',
  '.bg-amber-500',
  '.bg-amber-600',
  '.from-amber-600',
];

export const legacyThemePlugin = plugin((api) => {
  api.addBase({
    // Both themes: white text on these legacy button colors was below 4.5:1.
    '.legacy-scope': {
      [varName('lbg', 'emerald', 600)]: channels('emerald', 700),
      [varName('lbg', 'amber', 600)]: channels('amber', 700),
    },
    'html[data-theme="dark"] .legacy-scope': {
      [varName('ltx', 'slate', 500)]: channels('slate', 400),
      [varName('ltx', 'slate', 600)]: channels('slate', 400),
      [varName('ltx', 'emerald', 600)]: channels('emerald', 400),
      [varName('ltx', 'indigo', 600)]: channels('indigo', 400),
      [varName('lph', 'slate', 500)]: channels('slate', 400),
    },
    'html[data-theme="light"] .legacy-scope': lightDeclarations(),
    [`html[data-theme="light"] .legacy-scope :is(${SOLID_BACKGROUNDS.join(', ')})`]: resetTextDeclarations(),
  });
});
