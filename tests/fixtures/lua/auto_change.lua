-- Test helper for tests/test_prefs.py: changes remembered options the way mpv and other scripts do automatically while
-- a file loads (profiles, per-file configs...). mu-prefs must not store any of these.
local mp = require('mp')

mp.register_event('start-file', function() mp.set_property_number('sub-scale', 2.5) end)
mp.register_event('file-loaded', function() mp.set_property_number('brightness', 10) end)
