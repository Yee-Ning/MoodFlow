-- Include PG fallback increments in the hot base without flushing them twice.
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if redis.call('HGET', KEYS[2], 'epoch') ~= ARGV[1] then return 0 end
local base = redis.call('HGET', KEYS[2], 'base')
if base ~= ARGV[2] then return 0 end
-- Integer arithmetic uses Redis INCRBY, avoiding Lua double precision loss.
redis.call('INCRBY', KEYS[1], ARGV[3])
redis.call('HSET', KEYS[2], 'base', ARGV[4])
return 1
