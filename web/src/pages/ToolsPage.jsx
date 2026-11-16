import { TOOL_BY_PATH, toolBySlug } from '../data/toolsCatalog.js';
import ToolsHub from './tools/ToolsHub.jsx';
import OddsConversionTool from './tools/OddsConversionTool.jsx';
import ParlayTool from './tools/ParlayTool.jsx';
import HoldTool from './tools/HoldTool.jsx';
import NoVigTool from './tools/NoVigTool.jsx';
import ClvTool from './tools/ClvTool.jsx';
import KellyTool from './tools/KellyTool.jsx';

const BY_SLUG = {
  'odds-conversion-calculator': OddsConversionTool,
  'parlay-calculator': ParlayTool,
  'hold-calculator': HoldTool,
  'no-vig-calculator': NoVigTool,
  'clv-calculator': ClvTool,
  'kelly-criterion-calculator': KellyTool,
};

export default function ToolsPage({ path }) {
  const clean = (path || '').replace(/\/+$/, '') || '/tools';
  if (clean === '/tools') return <ToolsHub />;
  const meta = TOOL_BY_PATH[clean] || toolBySlug(clean.split('/').pop());
  const Comp = meta ? BY_SLUG[meta.slug] : null;
  if (!Comp) return <ToolsHub />;
  return <Comp />;
}
