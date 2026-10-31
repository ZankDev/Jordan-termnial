#region Using declarations
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
#endregion

// NOSTRO BRIDGE — a small HTTP server inside NinjaTrader 8 (127.0.0.1:8099, this PC only) that the NT LAB loop talks to:
//   GET /ping                               NinjaTrader is up; connections and their status; accounts; the lab folders
//   GET /bars?instrument=ES 12-26&type=minute&value=5&from=2026-01-01&to=2026-10-01[&hours=CME US Index Futures ETH]
//        historical bars straight from the connected data provider (JSON; up to 400,000 bars)
//   GET /results?batch=<id>                 which result files of a batch exist yet
// Backtests themselves run in Strategy Analyzer with the NostroLab strategy (see ninjatrader/SPEC.md).
// A plain TcpListener (not HttpListener), so it needs no admin rights or URL reservations. C# 5 syntax on purpose.
namespace NinjaTrader.NinjaScript.AddOns
{
	public class NostroBridge : NinjaTrader.NinjaScript.AddOnBase
	{
		public const int Port = 8099;
		private static TcpListener listener;
		private static Thread thread;
		private static readonly object gate = new object();

		protected override void OnStateChange()
		{
			if (State == NinjaTrader.NinjaScript.State.SetDefaults)
			{
				Name = "NostroBridge";
				Description = "Local HTTP bridge for the NOSTRO NT LAB backtest loop";
			}
			else if (State == NinjaTrader.NinjaScript.State.Active || State == NinjaTrader.NinjaScript.State.Configure)
				Start();
			else if (State == NinjaTrader.NinjaScript.State.Terminated)
				Stop();
		}

		private static string Lab() { return Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "nostro"); }

		private void Start()
		{
			lock (gate)
			{
				if (listener != null) return;
				Directory.CreateDirectory(Path.Combine(Lab(), "jobs"));
				Directory.CreateDirectory(Path.Combine(Lab(), "results"));
				listener = new TcpListener(IPAddress.Loopback, Port);
				listener.Start();
				thread = new Thread(Loop) { IsBackground = true, Name = "NostroBridge" };
				thread.Start();
				File.WriteAllText(Path.Combine(Lab(), "bridge.json"), "{\"port\":" + Port + ",\"started\":\"" + DateTime.Now.ToString("s", CultureInfo.InvariantCulture) + "\"}");
				Print("NostroBridge listening on 127.0.0.1:" + Port);
			}
		}

		private static void Stop()
		{
			lock (gate)
			{
				if (listener == null) return;
				try { listener.Stop(); } catch { }
				listener = null;
			}
		}

		private static void Loop()
		{
			while (true)
			{
				TcpClient c;
				try { TcpListener l = listener; if (l == null) return; c = l.AcceptTcpClient(); }
				catch { return; }
				ThreadPool.QueueUserWorkItem(delegate { Handle(c); });
			}
		}

		private static void Handle(TcpClient client)
		{
			using (client)
			{
				try
				{
					client.ReceiveTimeout = 10000;
					NetworkStream ns = client.GetStream();
					StreamReader rd = new StreamReader(ns, Encoding.UTF8);
					string line = rd.ReadLine();
					if (string.IsNullOrEmpty(line)) return;
					while (!string.IsNullOrEmpty(rd.ReadLine())) { }  // headers (GET only)
					string[] parts = line.Split(' ');
					string target = parts.Length > 1 ? parts[1] : "/";
					string path = target, query = "";
					int q = target.IndexOf('?');
					if (q >= 0) { path = target.Substring(0, q); query = target.Substring(q + 1); }
					Dictionary<string, string> args = ParseQuery(query);
					int status = 200;
					string body;
					try
					{
						if (path == "/ping") body = Ping();
						else if (path == "/bars") body = BarsJson(args);
						else if (path == "/results") body = Results(args);
						else { status = 404; body = "{\"error\":\"unknown path\"}"; }
					}
					catch (Exception e) { status = 500; body = "{\"error\":" + Q(e.Message) + "}"; }
					byte[] payload = Encoding.UTF8.GetBytes(body);
					string head = "HTTP/1.1 " + status + (status == 200 ? " OK" : " ERR") + "\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: " + payload.Length + "\r\nConnection: close\r\n\r\n";
					byte[] h = Encoding.ASCII.GetBytes(head);
					ns.Write(h, 0, h.Length);
					ns.Write(payload, 0, payload.Length);
				}
				catch { }
			}
		}

		private static string Ping()
		{
			StringBuilder sb = new StringBuilder("{\"ok\":true,\"bridge\":\"NostroBridge 1.0\",\"time\":");
			sb.Append(Q(DateTime.Now.ToString("s", CultureInfo.InvariantCulture))).Append(",\"tz\":").Append(Q(TimeZoneInfo.Local.Id)).Append(",\"lab\":").Append(Q(Lab())).Append(",\"connections\":[");
			bool first = true;
			lock (Connection.Connections)
				foreach (Connection c in Connection.Connections)
				{
					if (!first) sb.Append(',');
					first = false;
					sb.Append("{\"name\":").Append(Q(c.Options.Name)).Append(",\"status\":").Append(Q(c.Status.ToString())).Append('}');
				}
			sb.Append("],\"accounts\":[");
			first = true;
			lock (Account.All)
				foreach (Account a in Account.All)
				{
					if (!first) sb.Append(',');
					first = false;
					sb.Append(Q(a.Name));
				}
			return sb.Append("]}").ToString();
		}

		private static string BarsJson(Dictionary<string, string> a)
		{
			string name = Get(a, "instrument", "");
			Instrument inst = Instrument.GetInstrument(name);
			if (inst == null) throw new ArgumentException("unknown instrument '" + name + "' (use NinjaTrader's name, e.g. 'ES 12-26')");
			CultureInfo ci = CultureInfo.InvariantCulture;
			DateTime from = DateTime.Parse(Get(a, "from", DateTime.Today.AddDays(-30).ToString("yyyy-MM-dd")), ci);
			DateTime to = DateTime.Parse(Get(a, "to", DateTime.Today.AddDays(1).ToString("yyyy-MM-dd")), ci);
			BarsPeriodType type = (BarsPeriodType)Enum.Parse(typeof(BarsPeriodType), Get(a, "type", "Minute"), true);
			int value = int.Parse(Get(a, "value", "1"), ci);
			string err = null;
			Bars bars = null;
			ManualResetEvent done = new ManualResetEvent(false);
			BarsRequest req = new BarsRequest(inst, from, to);
			req.BarsPeriod = new BarsPeriod { BarsPeriodType = type, Value = value };
			string hours = Get(a, "hours", "");
			if (hours.Length > 0) req.TradingHours = TradingHours.Get(hours);
			req.Request(delegate(BarsRequest r, ErrorCode code, string msg)
			{
				if (code != ErrorCode.NoError) err = code + ": " + msg; else bars = r.Bars;
				done.Set();
			});
			if (!done.WaitOne(TimeSpan.FromSeconds(120))) throw new TimeoutException("the data provider did not answer within 120 s");
			if (err != null) { req.Dispose(); throw new InvalidOperationException(err); }
			StringBuilder sb = new StringBuilder("{\"instrument\":");
			sb.Append(Q(inst.FullName)).Append(",\"point_value\":").Append(inst.MasterInstrument.PointValue.ToString(ci)).Append(",\"tick\":").Append(inst.MasterInstrument.TickSize.ToString(ci));
			sb.Append(",\"tz\":").Append(Q(TimeZoneInfo.Local.Id)).Append(",\"bars\":[");
			int n = Math.Min(bars.Count, 400000), start = bars.Count - n;
			for (int i = start; i < bars.Count; i++)
			{
				if (i > start) sb.Append(',');
				sb.Append('[').Append(Q(bars.GetTime(i).ToString("s", ci))).Append(',').Append(bars.GetOpen(i).ToString(ci)).Append(',').Append(bars.GetHigh(i).ToString(ci))
				  .Append(',').Append(bars.GetLow(i).ToString(ci)).Append(',').Append(bars.GetClose(i).ToString(ci)).Append(',').Append(bars.GetVolume(i).ToString(ci)).Append(']');
			}
			req.Dispose();
			return sb.Append("]}").ToString();
		}

		private static string Results(Dictionary<string, string> a)
		{
			string batch = Get(a, "batch", "");
			if (batch.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || batch.Contains("..")) throw new ArgumentException("bad batch id");
			string dir = Path.Combine(Path.Combine(Lab(), "results"), batch);
			StringBuilder sb = new StringBuilder("{\"batch\":").Append(Q(batch)).Append(",\"done\":[");
			if (Directory.Exists(dir))
			{
				bool first = true;
				foreach (string f in Directory.GetFiles(dir, "*.json"))
				{
					if (!first) sb.Append(',');
					first = false;
					sb.Append(Path.GetFileNameWithoutExtension(f));
				}
			}
			return sb.Append("]}").ToString();
		}

		private static Dictionary<string, string> ParseQuery(string q)
		{
			Dictionary<string, string> d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
			foreach (string kv in q.Split('&'))
			{
				if (kv.Length == 0) continue;
				int e = kv.IndexOf('=');
				string k = Uri.UnescapeDataString((e < 0 ? kv : kv.Substring(0, e)).Replace('+', ' '));
				d[k] = e < 0 ? "" : Uri.UnescapeDataString(kv.Substring(e + 1).Replace('+', ' '));
			}
			return d;
		}

		private static string Get(Dictionary<string, string> d, string k, string def) { string v; return d.TryGetValue(k, out v) && v.Length > 0 ? v : def; }

		private static string Q(string s)
		{
			if (s == null) return "null";
			StringBuilder b = new StringBuilder("\"");
			foreach (char ch in s)
			{
				if (ch == '"' || ch == '\\') b.Append('\\').Append(ch);
				else if (ch < ' ') b.Append("\\u").Append(((int)ch).ToString("x4"));
				else b.Append(ch);
			}
			return b.Append('"').ToString();
		}
	}
}
