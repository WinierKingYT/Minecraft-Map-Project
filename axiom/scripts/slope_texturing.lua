-- Axiom Script Brush: re-texture the surface by local slope (paint over terrain).
-- Uses only documented API: getHighestBlockYAt, getBlock, setBlock, isSolid, getSimplexNoise, blocks.*
rockSlope = $int(Rock above slope (blocks),3,1,8)$
screeSlope = $int(Scree above slope (blocks),2,1,8)$
seed = $int(Noise seed,7,0,9999)$
top = getHighestBlockYAt(x, z)
if y ~= top then return nil end
local d = math.max(math.abs(getHighestBlockYAt(x+1,z) - getHighestBlockYAt(x-1,z)),
                   math.abs(getHighestBlockYAt(x,z+1) - getHighestBlockYAt(x,z-1)))
local n = getSimplexNoise(x/12, 0, z/12, seed)
if d >= rockSlope then
  if n > 0.6 then return blocks.andesite elseif n > 0.35 then return blocks.stone else return blocks.cobblestone end
elseif d >= screeSlope then
  if n > 0.5 then return blocks.gravel else return blocks.coarse_dirt end
end
return nil
