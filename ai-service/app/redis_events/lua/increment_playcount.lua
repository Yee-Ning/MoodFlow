-- Missing either key means restore the last PG flush before incrementing.
if redis.call('EXISTS', KEYS[1]) == 0 or redis.call('EXISTS', KEYS[2]) == 0 then
    return false
end
redis.call('INCR', KEYS[1])
-- GET returns the integer as a string; Lua numeric replies round above 2^53.
return redis.call('GET', KEYS[1])
