-- KEYS[1] = secret:<token>

local state = redis.call('HGET', KEYS[1], 'state')

if not state then
    return {0, 'NOT_FOUND'}
end

if state ~= 'AVAILABLE' then
    return {0, 'ALREADY_TAKEN'}
end

redis.call('HSET', KEYS[1], 'state', 'CLAIMED')

local file_id = redis.call('HGET', KEYS[1], 'file_id')
return {1, file_id}
