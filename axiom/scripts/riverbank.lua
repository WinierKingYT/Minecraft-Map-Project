-- Axiom Script Brush: river banks - gravel/clay/sand mix on blocks touching water.
seed = $int(Noise seed,5,0,9999)$
local b = getBlock(x, y, z)
if not isSolid(b) or getBlock(x, y + 1, z) == blocks.water then return nil end
local wet = getBlock(x+1,y,z) == blocks.water or getBlock(x-1,y,z) == blocks.water or
            getBlock(x,y,z+1) == blocks.water or getBlock(x,y,z-1) == blocks.water or
            getBlock(x+1,y+1,z) == blocks.water or getBlock(x-1,y+1,z) == blocks.water
if not wet then return nil end
local n = getSimplexNoise(x/6, y/6, z/6, seed)
if n > 0.7 then return blocks.clay elseif n > 0.4 then return blocks.gravel else return blocks.sand end
