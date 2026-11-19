
var DIR = "C:/Users/faruk/Desktop/mcmap/Minecraft-Terrain-AI/exports/fantasy_valley_256-b0a8d75fca52";
var hm = wp.getHeightMap().fromFile(DIR + '/heightmap16.png').go();
var fmt = wp.getMapFormat().withId("org.pepsoft.anvil.1.20.5").go();
var world = wp.createWorld().fromHeightMap(hm)
    .fromLevels(0, 65535).toLevels(0, 320)
    .withWaterLevel(62).withMapFormat(fmt)
    .withLowerBuildLimit(-64).withUpperBuildLimit(320).go();
world.setName("fantasy_valley_256");
try { world.setSpawnPoint(new java.awt.Point(128, 128)); } catch (e) { print('spawn not set: ' + e); }
var tm = wp.getHeightMap().fromFile(DIR + '/terrain_index.png').go();
var op = wp.applyHeightMap(tm).toWorld(world).applyToTerrain();
var TV = Java.type('org.pepsoft.worldpainter.Terrain').VALUES;
function tIdx(n) { for (var i = 0; i < TV.length; i++) if (TV[i].name() == n) return i; throw 'no terrain ' + n; }
op = op.fromLevel(0).toTerrain(tIdx('GRASS'));
op = op.fromLevel(2).toTerrain(tIdx('SAND'));
op = op.fromLevel(5).toTerrain(tIdx('ROCK'));
op = op.fromLevel(10).toTerrain(tIdx('GRAVEL'));
op = op.fromLevel(49).toTerrain(tIdx('PERMADIRT'));
op = op.fromLevel(50).toTerrain(tIdx('PODZOL'));
op = op.fromLevel(74).toTerrain(tIdx('ANDESITE'));
op = op.fromLevel(75).toTerrain(tIdx('STONE_MIX'));
op = op.fromLevel(160).toTerrain(tIdx('MOSS'));
op.go();
var layers = {'Deciduous': 'trees_deciduous.png', 'Pine': 'trees_pine.png'};
for (var name in layers) {
    var m = wp.getHeightMap().fromFile(DIR + '/' + layers[name]).go();
    var layer = wp.getLayer().withName(name).go();  // must be fetched before applyHeightMap() starts
    wp.applyHeightMap(m).toWorld(world).applyToLayer(layer)
        .fromLevels(0, 15).toLevels(0, 15).go();
}
wp.saveWorld(world).toFile(DIR + '/worldpainter/' + "fantasy_valley_256" + '.world').go();
wp.exportWorld(world).toDirectory(DIR + '/world').go();
print('WP_EXPORT_OK');
