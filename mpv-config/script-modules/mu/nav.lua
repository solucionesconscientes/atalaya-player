-- Shared menu navigation for the mu-* scripts (H15, ADR-041): breadcrumbs in the menu title, an "Atrás" first row,
-- ←/⌫ go back one level, and leaving the root of a module returns to whoever opened it (the main menu by default).
-- Every mpv script runs in its own Lua state, so the navigation stack is shared by hand-off: the opener sends
-- `script-message-to <script> mu-nav-open <entry> <json {crumbs, script, view}>` and the module remembers that parent
-- until the user leaves it. Verified against uosc 5.13: the title is only drawn on a root menu; ⌫ (and the mouse back
-- button) at a root send {type:'back'}; ← at a root without a search arrives as {type:'key', id:'left'};
-- `script-binding uosc/menu-back` goes to the parent submenu or, at a root, sends 'back'.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')

local M = {}

M.SEP = ' › '
M.HOME = 'MPV-UOS'
M.MAIN_SCRIPT = 'mu_menu'
M.MAX_CRUMBS = 4

function M.default_parent()
  return { crumbs = { M.HOME }, script = M.MAIN_SCRIPT, view = 'root' }
end

-- "MPV-UOS › TV y radio › España": empty and repeated neighbours dropped, long trails shortened in the middle.
function M.join(crumbs)
  local list = {}
  for _, c in ipairs(crumbs or {}) do
    c = tostring(c or '')
    if c ~= '' and c ~= list[#list] then list[#list + 1] = c end
  end
  if #list > M.MAX_CRUMBS then
    local short = { list[1], '…' }
    for i = #list - (M.MAX_CRUMBS - 3), #list do short[#short + 1] = list[i] end
    list = short
  end
  return table.concat(list, M.SEP)
end

function M.back_item()
  return { title = 'Atrás', icon = 'arrow_back', hint = '⌫', value = { nav = 'back' }, keep_open = true }
end

local function is_back_value(v) return type(v) == 'table' and v.nav == 'back' end

-- Adds the back row on top of a root menu and keeps the keyboard selection where it was (the first active or
-- selectable item, one row lower). Palettes are left alone: ⌫ on an empty query already goes back.
function M.decorate(menu)
  if menu.search_style == 'palette' then return menu end
  local items = menu.items or {}
  if items[1] and is_back_value(items[1].value) then return menu end
  local sel = menu.selected_index
  if not sel then
    for i, it in ipairs(items) do if it.active then sel = i break end end
  end
  if not sel then
    for i, it in ipairs(items) do if it.selectable ~= false then sel = i break end end
  end
  local out = { M.back_item() }  -- a copy: callers may show the same items table again
  for _, it in ipairs(items) do out[#out + 1] = it end
  menu.items = out
  menu.selected_index = sel and sel + 1 or 1
  return menu
end

-- Is this uosc callback event a "go back one level" request? The back row is answered with uosc/menu-back so that
-- inside a uosc submenu it returns to the parent submenu and at a root it comes back as a 'back' event.
function M.is_back_row(ev)
  return type(ev) == 'table' and ev.type == 'activate' and is_back_value(ev.value)
end

function M.is_back(ev)
  return type(ev) == 'table' and (ev.type == 'back' or (ev.type == 'key' and ev.id == 'left'))
end

function M.menu_back() mp.commandv('script-binding', 'uosc/menu-back') end

-- Normalises the three ways back: returns true for 'back' and ←, and turns a click on the back row into menu-back
-- (handled = true, nothing else to do). Usage: `local back, handled = nav.classify(ev)`.
function M.classify(ev)
  if M.is_back_row(ev) then
    M.menu_back()
    return false, true
  end
  return M.is_back(ev), false
end

local Nav = {}
Nav.__index = Nav

function M.new()
  local self = setmetatable({ script = mp.get_script_name(), parent = M.default_parent(), entries = {} }, Nav)
  mp.register_script_message('mu-nav-open', function(entry, json)
    local fn = self.entries[entry or '']
    if not fn then msg.warn('mu-nav-open: unknown entry ' .. tostring(entry)) return end
    local from = utils.parse_json(json or '')
    if type(from) == 'table' and type(from.crumbs) == 'table' and from.script then
      self.parent = from
    else
      self.parent = M.default_parent()
    end
    fn()
  end)
  return self
end

-- An entry point other scripts can open with mu-nav-open (keeps the parent they pass).
function Nav:entry(name, fn) self.entries[name] = fn end

-- A key binding that opens a menu: opened from the keyboard, "back" at its root goes to the main menu.
function Nav:binding(name, fn, flags)
  self.entries[name] = fn
  mp.add_key_binding(nil, name, function(...)
    self.parent = M.default_parent()
    fn(...)
  end, flags)
end

-- Breadcrumb title: the parent's crumbs followed by the module's own levels (outermost first).
function Nav:title(levels)
  local crumbs = {}
  for _, c in ipairs(self.parent.crumbs or {}) do crumbs[#crumbs + 1] = c end
  for _, c in ipairs(levels or {}) do crumbs[#crumbs + 1] = c end
  local title = M.join(crumbs)
  mp.set_property_native('user-data/mu/nav', { script = self.script, title = title, depth = #(levels or {}),
                                               parent = self.parent.script or '', at = mp.get_time() })
  return title
end

-- Title and back row of a module menu built from a view stack ({name, title?, …}, current view last): the current
-- view's own title is remembered in its stack entry so that deeper views can show it as a crumb.
function Nav:frame(menu, stack)
  local top = stack[#stack]
  if top then top.title = menu.title end
  local lv = {}
  for _, spec in ipairs(stack) do lv[#lv + 1] = spec.title end
  menu.title = self:title(lv)
  return M.decorate(menu)
end

-- The module's own stack is empty: hand the menu back to the opener. Returns false when there is nobody to return
-- to (the caller then closes its menu).
function Nav:leave()
  local p = self.parent
  self.parent = M.default_parent()
  if not p or not p.script or p.script == '' or p.script == self.script then return false end
  mp.commandv('script-message-to', p.script, 'mu-nav-return', p.view or 'root')
  return true
end

-- What the main menu passes to a module it opens.
function M.handoff(crumbs, view)
  return utils.format_json({ crumbs = crumbs, script = mp.get_script_name(), view = view or 'root' })
end

-- Opens `entry` of `script` as a child of the caller.
function M.open_child(script, entry, crumbs, view)
  mp.commandv('script-message-to', script, 'mu-nav-open', entry, M.handoff(crumbs, view))
end

return M
