-- Axiom Script Brush: forest floor - podzol/moss/coarse dirt patches with ferns on grass.
podzolAmount = $float(Podzol amount,0.45,0,1)$
plantChance = $float(Plant chance,0.12,0,1)$
seed = $int(Noise seed,21,0,9999)$
if getBlock(x, y, z) ~= blocks.grass_block or getBlock(x, y + 1, z) ~= blocks.air then return nil end
local n = getSimplexNoise(x/14, 0, z/14, seed)
local fine = getSimplexNoise(x*0.7, y, z*0.7, seed + 3)
if n < podzolAmount * 0.6 then setBlock(x, y, z, blocks.podzol)
elseif n < podzolAmount * 0.75 then setBlock(x, y, z, blocks.moss_block)
elseif n > 0.85 then setBlock(x, y, z, blocks.coarse_dirt) end
if fine < plantChance then
  if fine < plantChance * 0.5 then setBlock(x, y + 1, z, blocks.fern) else setBlock(x, y + 1, z, blocks.short_grass) end
end
return nil
