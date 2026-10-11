-- One hash per identity: O(1) storage and a single Redis Cluster slot.
-- ARGV: limit, window_ms, optional test-only timestamp_ms. Production uses TIME.
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
if not now then
    local clock = redis.call('TIME')
    now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
end
local bucket = math.floor(now / window)
local elapsed = now % window
local saved = redis.call('HMGET', KEYS[1], 'bucket', 'current', 'previous', 'window_ms')
local old_bucket = tonumber(saved[1])
local current = tonumber(saved[2]) or 0
local previous = tonumber(saved[3]) or 0
if old_bucket and tonumber(saved[4]) ~= window then
    -- A configured window change starts a new policy epoch; bucket indices
    -- from different window lengths cannot be compared as wall-clock time.
    old_bucket, current, previous = nil, 0, 0
end
if old_bucket then
    if bucket == old_bucket + 1 then
        previous, current = current, 0
    elseif bucket > old_bucket + 1 then
        previous, current = 0, 0
    elseif bucket < old_bucket then
        -- A backwards server clock must not reset an already consumed window.
        bucket, elapsed = old_bucket, 0
    end
end
-- Integer weighted units avoid rounding the estimate down at the boundary.
local remaining = window - elapsed
local accepted = 0
local retry_ms = 0
if current * window + previous * remaining + window <= limit * window then
    current = current + 1
    accepted = 1
else
    local capacity = limit - current - 1
    if previous > 0 and capacity >= 0 then
        retry_ms = math.ceil((previous * remaining - capacity * window) / previous)
    else
        -- At rollover current becomes previous; include that decay as well.
        retry_ms = remaining
        if current > limit - 1 then
            retry_ms = retry_ms + math.ceil(window * (current - limit + 1) / current)
        end
    end
end
redis.call('HSET', KEYS[1], 'bucket', bucket, 'current', current, 'previous', previous, 'window_ms', window)
redis.call('PEXPIRE', KEYS[1], window * 2)
return {accepted, math.max(0, retry_ms)}
