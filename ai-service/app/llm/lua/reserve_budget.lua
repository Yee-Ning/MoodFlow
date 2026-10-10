-- Counters contain consumed + reserved tokens, rather than remaining capacity.
-- All keys share the run hash tag. No mutation occurs on either rejection.
-- KEYS: L2, L3, reservation receipts. ARGV: amount, L2 cap, L3 cap,
-- call id, receipt JSON, lifecycle TTL seconds.
local l2 = tonumber(redis.call('GET', KEYS[1]))
local l3 = tonumber(redis.call('GET', KEYS[2]))
if not l2 or not l3 then return 'rebuild' end
if redis.call('HEXISTS', KEYS[3], ARGV[4]) == 1 then return 'accepted' end
local amount = tonumber(ARGV[1])
-- Preserve Phase 2's task-first signal when both layers would reject.
if l3 + amount > tonumber(ARGV[3]) then return 'l3' end
if l2 + amount > tonumber(ARGV[2]) then return 'l2' end
redis.call('INCRBYFLOAT', KEYS[1], ARGV[1])
redis.call('INCRBYFLOAT', KEYS[2], ARGV[1])
redis.call('HSET', KEYS[3], ARGV[4], ARGV[5])
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, ARGV[6]) end
return 'accepted'
