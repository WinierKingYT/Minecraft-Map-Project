package terrainai.bridge;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.MessageScreen;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.client.util.ScreenshotRecorder;
import net.minecraft.server.command.CommandOutput;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.integrated.IntegratedServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Supplier;
import java.util.regex.Pattern;

/**
 * Localhost-only HTTP bridge (127.0.0.1, token header) so a local agent can drive the dev client.
 * Requests are served one at a time; game state is touched only on the client / server threads.
 */
public class BridgeMod implements ClientModInitializer {
	static final Logger LOG = LoggerFactory.getLogger("terrain_bridge");
	static final int PORT = Integer.getInteger("terrainbridge.port", 25580);
	static final String TOKEN_HEADER = "X-Bridge-Token";
	static final int MAX_BODY = 64 * 1024;
	static final Pattern NAME = Pattern.compile("[A-Za-z0-9_\\-]{1,64}");
	static final Gson GSON = new Gson();
	static final long STABLE_TICKS = 40;  // chunk count unchanged this long (2 s) = render settled

	static volatile long ticks;
	private byte[] token;

	static final class HttpError extends Exception {
		final int status;
		HttpError(int status, String msg) { super(msg); this.status = status; }
	}

	@Override
	public void onInitializeClient() {
		ClientTickEvents.END_CLIENT_TICK.register(c -> ticks++);
		try {
			byte[] raw = new byte[16];
			new SecureRandom().nextBytes(raw);
			String hex = HexFormat.of().formatHex(raw);
			token = hex.getBytes(StandardCharsets.US_ASCII);
			HttpServer srv = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), PORT), 8);
			srv.createContext("/", this::handle);
			srv.setExecutor(Executors.newSingleThreadExecutor(r -> {
				Thread t = new Thread(r, "terrain-bridge-http");
				t.setDaemon(true);
				return t;
			}));
			srv.start();
			Path dir = MinecraftClient.getInstance().runDirectory.toPath();
			Path tmp = dir.resolve("bridge_token.txt.tmp");
			Files.writeString(tmp, hex + "\n" + PORT + "\n");
			Files.move(tmp, dir.resolve("bridge_token.txt"), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
			LOG.info("terrain bridge listening on 127.0.0.1:{}", PORT);
		} catch (IOException e) {
			LOG.error("terrain bridge failed to start", e);
		}
	}

	private void handle(HttpExchange ex) throws IOException {
		int status = 200;
		JsonObject res;
		try {
			if (!ex.getRemoteAddress().getAddress().isLoopbackAddress()) throw new HttpError(403, "loopback only");
			String got = ex.getRequestHeaders().getFirst(TOKEN_HEADER);
			if (got == null || !MessageDigest.isEqual(token, got.trim().getBytes(StandardCharsets.US_ASCII)))
				throw new HttpError(401, "missing or bad token");
			JsonObject body = readBody(ex);
			res = route(ex.getRequestMethod(), ex.getRequestURI().getPath(), body);
			res.addProperty("ok", true);
		} catch (HttpError e) {
			status = e.status;
			res = error(e.getMessage());
		} catch (TimeoutException e) {
			status = 504;
			res = error("timed out");
		} catch (Exception e) {
			LOG.warn("bridge request failed", e);
			status = 500;
			res = error(e.getClass().getSimpleName() + ": " + e.getMessage());
		}
		byte[] out = GSON.toJson(res).getBytes(StandardCharsets.UTF_8);
		ex.getResponseHeaders().set("Content-Type", "application/json");
		ex.sendResponseHeaders(status, out.length);
		ex.getResponseBody().write(out);
		ex.close();
		if (res.has("shutdown")) MinecraftClient.getInstance().scheduleStop();
	}

	private static JsonObject error(String msg) {
		JsonObject o = new JsonObject();
		o.addProperty("ok", false);
		o.addProperty("error", msg);
		return o;
	}

	private static JsonObject readBody(HttpExchange ex) throws IOException, HttpError {
		try (InputStream in = ex.getRequestBody()) {
			byte[] b = in.readNBytes(MAX_BODY + 1);
			if (b.length > MAX_BODY) throw new HttpError(413, "body too large");
			String s = new String(b, StandardCharsets.UTF_8).trim();
			if (s.isEmpty()) return new JsonObject();
			try {
				return JsonParser.parseString(s).getAsJsonObject();
			} catch (RuntimeException e) {
				throw new HttpError(400, "body must be a JSON object");
			}
		}
	}

	private JsonObject route(String method, String path, JsonObject b) throws Exception {
		boolean post = method.equals("POST");
		switch (path) {
			case "/status": return status();
			case "/open_world": if (post) return openWorld(str(b, "name"), num(b, "timeout", 240)); break;
			case "/command": if (post) return command(str(b, "cmd")); break;
			case "/camera": if (post) return camera(b); break;
			case "/screenshot": if (post) return screenshot(b); break;
			case "/quit_world": if (post) return quitWorld(); break;
			case "/shutdown": if (post) { JsonObject o = new JsonObject(); o.addProperty("shutdown", true); return o; } break;
			default: throw new HttpError(404, "unknown endpoint " + path);
		}
		throw new HttpError(405, "use POST for " + path);
	}

	// ---- helpers -------------------------------------------------------------------------------------------

	static MinecraftClient mc() { return MinecraftClient.getInstance(); }

	static <T> T onClient(Supplier<T> s, long ms) throws Exception {
		return mc().submit(s).get(ms, TimeUnit.MILLISECONDS);
	}

	static String str(JsonObject b, String k) throws HttpError {
		if (!b.has(k) || !b.get(k).isJsonPrimitive()) throw new HttpError(400, "missing string field '" + k + "'");
		return b.get(k).getAsString();
	}

	static double num(JsonObject b, String k, double dflt) throws HttpError {
		if (!b.has(k)) return dflt;
		try {
			double v = b.get(k).getAsDouble();
			if (!Double.isFinite(v)) throw new NumberFormatException();
			return v;
		} catch (RuntimeException e) {
			throw new HttpError(400, "field '" + k + "' must be a number");
		}
	}

	static void sleep(long ms) throws InterruptedException { Thread.sleep(ms); }

	// ---- endpoints -----------------------------------------------------------------------------------------

	JsonObject status() throws Exception {
		return onClient(() -> {
			MinecraftClient c = mc();
			JsonObject o = new JsonObject();
			o.addProperty("screen", c.currentScreen == null ? null : c.currentScreen.getClass().getSimpleName());
			o.addProperty("loading", c.getOverlay() != null);
			o.addProperty("in_world", c.world != null && c.player != null);
			IntegratedServer s = c.getServer();
			if (s != null) o.addProperty("world", s.getSaveProperties().getLevelName());
			if (c.player != null) {
				o.addProperty("x", c.player.getX());
				o.addProperty("y", c.player.getY());
				o.addProperty("z", c.player.getZ());
				o.addProperty("yaw", c.player.getYaw());
				o.addProperty("pitch", c.player.getPitch());
			}
			if (c.world != null) {
				o.addProperty("chunks", c.worldRenderer.getCompletedChunkCount());
				o.addProperty("terrain_done", c.worldRenderer.isTerrainRenderComplete());
			}
			o.addProperty("fps", c.getCurrentFps());
			o.addProperty("pid", ProcessHandle.current().pid());
			return o;
		}, 5000);
	}

	JsonObject openWorld(String name, double timeoutS) throws Exception {
		if (!NAME.matcher(name).matches()) throw new HttpError(400, "world name must match " + NAME.pattern());
		String err = onClient(() -> {
			MinecraftClient c = mc();
			if (c.getOverlay() != null) return "game still loading, retry";
			if (c.world != null || c.getServer() != null) return "already in a world; quit_world first";
			if (!Files.isDirectory(c.getLevelStorage().getSavesDirectory().resolve(name).resolve("region")))
				return "no such save: " + name;
			c.createIntegratedServerLoader().start(name, () -> c.setScreen(new TitleScreen()));
			return null;
		}, 10000);
		if (err != null) throw new HttpError(409, err);
		long deadline = System.currentTimeMillis() + (long) (timeoutS * 1000);
		while (true) {
			Boolean in = onClient(() -> mc().world != null && mc().player != null && mc().currentScreen == null, 5000);
			if (in) break;
			if (System.currentTimeMillis() > deadline) {
				JsonObject st = status();
				throw new HttpError(504, "world did not open in time (screen=" + st.get("screen") + ")");
			}
			sleep(250);
		}
		JsonObject o = settle(Math.max(10, deadline - System.currentTimeMillis()));
		o.addProperty("world", name);
		return o;
	}

	/** Wait until the chunk builder is idle and the rendered chunk count stopped changing. */
	JsonObject settle(long timeoutMs) throws Exception {
		long t0 = System.currentTimeMillis(), deadline = t0 + timeoutMs;
		int last = -1;
		long stableSince = ticks;
		boolean settled = false;
		while (System.currentTimeMillis() < deadline) {
			int[] st = onClient(() -> mc().world == null ? new int[]{-1, 0}
					: new int[]{mc().worldRenderer.getCompletedChunkCount(), mc().worldRenderer.isTerrainRenderComplete() ? 1 : 0}, 5000);
			if (st[0] < 0) throw new HttpError(409, "left the world while waiting");
			if (st[0] != last || st[1] == 0) {
				last = st[0];
				stableSince = ticks;
			} else if (ticks - stableSince >= STABLE_TICKS && last > 0) {
				settled = true;
				break;
			}
			sleep(100);
		}
		JsonObject o = new JsonObject();
		o.addProperty("settled", settled);
		o.addProperty("chunks", last);
		o.addProperty("waited_ms", System.currentTimeMillis() - t0);
		return o;
	}

	JsonObject command(String cmd) throws Exception {
		String c0 = cmd.startsWith("/") ? cmd.substring(1) : cmd;
		if (c0.isBlank() || c0.length() > 32000) throw new HttpError(400, "empty or too long command");
		IntegratedServer s = onClient(() -> mc().getServer(), 5000);
		java.util.UUID id = onClient(() -> mc().player == null ? null : mc().player.getUuid(), 5000);
		if (s == null || id == null) throw new HttpError(409, "not in a singleplayer world");
		List<String> out = Collections.synchronizedList(new ArrayList<>());
		CommandOutput sink = new CommandOutput() {
			@Override public void sendMessage(Text message) { out.add(message.getString()); }
			@Override public boolean shouldReceiveFeedback() { return true; }
			@Override public boolean shouldTrackOutput() { return true; }
			@Override public boolean shouldBroadcastConsoleToOps() { return false; }
		};
		String err = s.submit(() -> {
			ServerPlayerEntity p = s.getPlayerManager().getPlayer(id);
			if (p == null) return "player not on server";
			// op level 4 source: works even though the exported level.dat has allowCommands 0
			ServerCommandSource src = p.getCommandSource().withOutput(sink).withLevel(4);
			s.getCommandManager().executeWithPrefix(src, c0);
			return null;
		}).get(15, TimeUnit.SECONDS);
		if (err != null) throw new HttpError(409, err);
		JsonObject o = new JsonObject();
		o.add("feedback", GSON.toJsonTree(new ArrayList<>(out)));
		return o;
	}

	JsonObject camera(JsonObject b) throws Exception {
		double x = num(b, "x", Double.NaN), y = num(b, "y", Double.NaN), z = num(b, "z", Double.NaN);
		if (Double.isNaN(x) || Double.isNaN(y) || Double.isNaN(z)) throw new HttpError(400, "x, y, z required");
		double yaw = num(b, "yaw", 0), pitch = Math.max(-90, Math.min(90, num(b, "pitch", 0)));
		boolean hide = !b.has("hide_hud") || b.get("hide_hud").getAsBoolean();
		List<String> fb = new ArrayList<>();
		for (String c : new String[]{"gamemode spectator",
				String.format(Locale.ROOT, "tp @s %.3f %.3f %.3f %.2f %.2f", x, y, z, yaw, pitch)}) {
			command(c).getAsJsonArray("feedback").forEach(e -> fb.add(e.getAsString()));
		}
		onClient(() -> { mc().options.hudHidden = hide; return null; }, 5000);
		JsonObject o = settle((long) (num(b, "timeout", 60) * 1000));
		o.add("feedback", GSON.toJsonTree(fb));
		return o;
	}

	JsonObject screenshot(JsonObject b) throws Exception {
		String name = b.has("name") ? str(b, "name") : "shot_" + System.currentTimeMillis();
		if (!NAME.matcher(name).matches()) throw new HttpError(400, "name must match " + NAME.pattern());
		int w = (int) num(b, "width", 0), h = (int) num(b, "height", 0);
		if (w != 0 || h != 0) {
			if (w < 320 || h < 240 || w > 3840 || h > 2160) throw new HttpError(400, "width/height out of range 320x240..3840x2160");
			onClient(() -> { mc().getWindow().setWindowedSize(w, h); return null; }, 5000);
		}
		JsonObject o = onClient(() -> mc().world != null, 5000) ? settle((long) (num(b, "timeout", 30) * 1000)) : new JsonObject();
		long until = ticks + (long) num(b, "frames", 10);  // extra frames after settling (fog, lighting)
		long deadline = System.currentTimeMillis() + 10000;
		while (ticks < until && System.currentTimeMillis() < deadline) sleep(50);
		CompletableFuture<String> done = new CompletableFuture<>();
		File file = new File(new File(mc().runDirectory, "screenshots"), name + ".png");
		onClient(() -> {
			ScreenshotRecorder.saveScreenshot(mc().runDirectory, name + ".png", mc().getFramebuffer(),
					msg -> done.complete(msg.getString()));
			return null;
		}, 10000);
		String msg = done.get(20, TimeUnit.SECONDS);
		if (!file.isFile()) throw new HttpError(500, "screenshot failed: " + msg);
		o.addProperty("path", file.getAbsolutePath());
		int[] size = onClient(() -> new int[]{mc().getFramebuffer().textureWidth, mc().getFramebuffer().textureHeight}, 5000);
		o.addProperty("width", size[0]);
		o.addProperty("height", size[1]);
		return o;
	}

	JsonObject quitWorld() throws Exception {
		onClient(() -> {
			MinecraftClient c = mc();
			if (c.world != null) {
				c.world.disconnect();
				c.disconnect(new MessageScreen(Text.translatable("menu.savingLevel")));
			}
			c.options.hudHidden = false;
			c.setScreen(new TitleScreen());
			return null;
		}, 120000);
		JsonObject o = new JsonObject();
		o.addProperty("in_world", onClient(() -> mc().world != null, 5000));
		return o;
	}
}
