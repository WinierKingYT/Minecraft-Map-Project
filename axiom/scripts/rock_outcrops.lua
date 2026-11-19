-- Axiom Script Brush: natural rock outcrops on exposed surface, blended by 3D noise.
-- Paint over a hillside; raises 1..height blocks of mixed stone where noise peaks.
height = $int(Max outcrop height,3,1,8)$
threshold = $float(Coverage threshold,0.62,0.3,0.95)$
seed = $int(Noise seed,11,0,9999)$
top = getHighestBlockYAt(x, z)
if y ~= top then return nil end
local n = getSimplexNoise(x/9, 0, z/9, seed)
if n < threshold then return nil end
local h = math.floor((n - threshold) / (1 - threshold) * height + 0.5)
local mix = { blocks.stone, blocks.andesite, blocks.tuff, blocks.cobblestone }
for i = 0, h do
  local m = getSimplexNoise(x/4, (y+i)/4, z/4, seed + 1)
  setBlock(x, y + i, z, mix[math.min(4, math.floor(m * 4) + 1)])
end
return nil
