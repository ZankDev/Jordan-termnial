#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

// NOSTRO LAB — one universal strategy that runs any candidate from the NT LAB search loop on NinjaTrader's own
// historical data, fill engine, commission and slippage.
//
// Batch mode (how the loop uses it): BatchId = "<batch>"; in Strategy Analyzer choose Optimize and let JobIndex run
// 0..N-1 — every iteration loads spec #JobIndex from <NT user folder>\nostro\jobs\<batch>.json, trades it, and on
// termination writes every trade to <NT user folder>\nostro\results\<batch>\<JobIndex>.json.
// Direct mode (BatchId empty): the strategy runs from its own properties, so NinjaTrader's genetic optimizer and
// Walk-Forward Optimization can search the same rules natively.
//
// Spec format: see ninjatrader/SPEC.md in the NOSTRO repo. All indicators are computed inside this file (no external
// indicator dependencies) so the rules are identical to the Python reference engine.
// Written in C# 5 syntax on purpose: it compiles in every NinjaTrader 8 version.
namespace NinjaTrader.NinjaScript.Strategies
{
	public class NostroLab : Strategy
	{
		private sealed class Spec
		{
			public string Id = "direct", Family = "orb", Dir = "both";
			public double RangeMin = 15, BufferTicks = 0, Fast = 9, Slow = 21, TrendLen = 0, Len = 20, Lo = 30, Hi = 70, K = 2, DevAtr = 1.5;
			public int OpenMin = 570, EntryFrom = 575, EntryTo = 690, Flat = 955;
			public double StopAtr = 1.5, TargetAtr = 3.0, TrailAtr = 0, MaxBars = 0, AtrLen = 14, MaxTradesDay = 2, Qty = 1;
		}

		private Spec spec;
		private TimeZoneInfo ntZone, nyZone;
		private readonly List<double> closes = new List<double>(), highs = new List<double>(), lows = new List<double>();
		private double atr, emaF, emaS, prevEmaF, prevEmaS, rsi, prevRsi, avgGain, avgLoss, prevClose = double.NaN;
		private int atrN, rsiN;
		private DateTime day = DateTime.MinValue;
		private int tradesToday, entryBar = -1;
		private double orbHi, orbLo, vwapPV, vwapV;
		private bool orbDone, orbStarted, ran;
		private DateTime firstBar = DateTime.MinValue, lastBar = DateTime.MinValue;
		private double prevUpper = double.NaN, prevLower = double.NaN;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description = "NOSTRO LAB universal spec-driven strategy (NT LAB search loop).";
				Name = "NostroLab";
				Calculate = Calculate.OnBarClose;
				EntriesPerDirection = 1;
				EntryHandling = EntryHandling.AllEntries;
				IsExitOnSessionCloseStrategy = true;
				ExitOnSessionCloseSeconds = 30;
				BarsRequiredToTrade = 20;
				IsInstantiatedOnEachOptimizationIteration = true;
				BatchId = "";
				JobIndex = 0;
				JobFolder = "";
				NtTimeZoneId = "";
				Family = "orb"; Direction = "both";
				RangeMin = 15; BufferTicks = 0; Fast = 9; Slow = 21; TrendLen = 0; Len = 20; Lo = 30; Hi = 70; BandK = 2; DevAtr = 1.5;
				SessionOpen = 930; EntryFrom = 935; EntryTo = 1130; FlatTime = 1555;
				StopAtr = 1.5; TargetAtr = 3.0; TrailAtr = 0; MaxBars = 0; AtrLen = 14; MaxTradesDay = 2; Qty = 1;
			}
			else if (State == State.Configure)
			{
				nyZone = FindZone("Eastern Standard Time");
				ntZone = string.IsNullOrEmpty(NtTimeZoneId) ? TimeZoneInfo.Local : FindZone(NtTimeZoneId);
				spec = string.IsNullOrEmpty(BatchId) ? FromProperties() : LoadSpec(BatchId, JobIndex);
			}
			else if (State == State.DataLoaded)
			{
				ran = true;
			}
			else if (State == State.Terminated)
			{
				if (ran && spec != null && !string.IsNullOrEmpty(BatchId) && SystemPerformance != null)
				{
					try { WriteResults(); }
					catch (Exception e) { Print("NostroLab: could not write results: " + e.Message); }
				}
			}
		}

		// ------------------------------------------------------------------ bars
		protected override void OnBarUpdate()
		{
			if (spec == null || BarsInProgress != 0)
				return;
			DateTime ny = TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(Time[0], DateTimeKind.Unspecified), ntZone, nyZone);
			if (firstBar == DateTime.MinValue) firstBar = ny;
			lastBar = ny;
			int minute = ny.Hour * 60 + ny.Minute;
			double c = Close[0], h = High[0], l = Low[0];

			if (ny.Date != day)
			{
				day = ny.Date; tradesToday = 0; orbDone = false; orbStarted = false; orbHi = double.MinValue; orbLo = double.MaxValue; vwapPV = 0; vwapV = 0;
			}
			UpdateIndicators(c, h, l);
			// session VWAP (from the session open) and the opening range
			if (minute > spec.OpenMin)
			{
				double v = Math.Max(1.0, Volume[0]);
				vwapPV += (h + l + c) / 3.0 * v; vwapV += v;
			}
			if (minute > spec.OpenMin && minute <= spec.OpenMin + (int)spec.RangeMin)
			{
				orbStarted = true; orbHi = Math.Max(orbHi, h); orbLo = Math.Min(orbLo, l);
			}
			else if (orbStarted && minute > spec.OpenMin + (int)spec.RangeMin)
				orbDone = true;

			// exits: flat time and the time stop
			if (Position.MarketPosition != MarketPosition.Flat)
			{
				if (entryBar < 0) entryBar = CurrentBar;
				bool timeUp = spec.MaxBars > 0 && CurrentBar - entryBar >= (int)spec.MaxBars;
				if (minute >= spec.Flat || timeUp)
				{
					if (Position.MarketPosition == MarketPosition.Long) ExitLong("TimeExit", "L");
					else ExitShort("TimeExit", "S");
				}
				Remember(c);
				return;
			}
			entryBar = -1;
			if (CurrentBar < BarsRequiredToTrade || atrN < (int)spec.AtrLen || minute < spec.EntryFrom || minute > spec.EntryTo || minute >= spec.Flat || tradesToday >= (int)spec.MaxTradesDay)
			{
				Remember(c);
				return;
			}
			int signal = Signal(c, h, l);
			if (signal > 0 && spec.Dir != "short") Enter(true);
			else if (signal < 0 && spec.Dir != "long") Enter(false);
			Remember(c);
		}

		private void Remember(double c)
		{
			prevClose = c; prevEmaF = emaF; prevEmaS = emaS; prevRsi = rsi;
		}

		private int Signal(double c, double h, double l)
		{
			int n = closes.Count;
			bool trendUp = true, trendDn = true;
			if (spec.TrendLen > 1 && n >= (int)spec.TrendLen)
			{
				double sma = Mean(closes, n - (int)spec.TrendLen, n);
				trendUp = c > sma; trendDn = c < sma;
			}
			switch (spec.Family)
			{
				case "orb":
				{
					if (!orbDone) return 0;
					double up = orbHi + spec.BufferTicks * TickSize, dn = orbLo - spec.BufferTicks * TickSize;
					if (c > up && !(prevClose > up)) return 1;
					if (c < dn && !(prevClose < dn)) return -1;
					return 0;
				}
				case "ema_cross":
					if (n < (int)spec.Slow + 2) return 0;
					if (prevEmaF <= prevEmaS && emaF > emaS && trendUp) return 1;
					if (prevEmaF >= prevEmaS && emaF < emaS && trendDn) return -1;
					return 0;
				case "donchian":
				{
					int len = (int)spec.Len;
					if (n < len + 2) return 0;
					double hh = Max(highs, n - 1 - len, n - 1), ll = Min(lows, n - 1 - len, n - 1);
					if (c > hh && trendUp) return 1;
					if (c < ll && trendDn) return -1;
					return 0;
				}
				case "rsi_revert":
					if (rsiN < (int)spec.Len + 1) return 0;
					if (prevRsi < spec.Lo && rsi >= spec.Lo && trendUp) return 1;
					if (prevRsi > spec.Hi && rsi <= spec.Hi && trendDn) return -1;
					return 0;
				case "bb_revert":
				{
					int len = (int)spec.Len;
					if (n < len + 2) return 0;
					double m = Mean(closes, n - len, n), sd = Std(closes, n - len, n, m);
					double upper = m + spec.K * sd, lower = m - spec.K * sd;
					int s = 0;
					if (!double.IsNaN(prevLower) && prevClose < prevLower && c > lower) s = 1;
					else if (!double.IsNaN(prevUpper) && prevClose > prevUpper && c < upper) s = -1;
					prevUpper = upper; prevLower = lower;
					return s;
				}
				case "vwap_revert":
				{
					if (vwapV <= 0) return 0;
					double vwap = vwapPV / vwapV;
					if (c < vwap - spec.DevAtr * atr && c > Open[0]) return 1;
					if (c > vwap + spec.DevAtr * atr && c < Open[0]) return -1;
					return 0;
				}
			}
			return 0;
		}

		private void Enter(bool isLong)
		{
			string sig = isLong ? "L" : "S";
			int stopT = Math.Max(1, (int)Math.Round(spec.StopAtr * atr / TickSize));
			int tgtT = Math.Max(1, (int)Math.Round(spec.TargetAtr * atr / TickSize));
			if (spec.TrailAtr > 0)
				SetTrailStop(sig, CalculationMode.Ticks, Math.Max(1, (int)Math.Round(spec.TrailAtr * atr / TickSize)), false);
			else
				SetStopLoss(sig, CalculationMode.Ticks, stopT, false);
			if (spec.TargetAtr > 0)
				SetProfitTarget(sig, CalculationMode.Ticks, tgtT);
			int qty = Math.Max(1, (int)spec.Qty);
			if (isLong) EnterLong(qty, sig); else EnterShort(qty, sig);
			tradesToday++;
		}

		// ------------------------------------------------------------------ indicators (Wilder ATR / RSI, EMA)
		private void UpdateIndicators(double c, double h, double l)
		{
			closes.Add(c); highs.Add(h); lows.Add(l);
			double tr = double.IsNaN(prevClose) ? h - l : Math.Max(h - l, Math.Max(Math.Abs(h - prevClose), Math.Abs(l - prevClose)));
			int an = (int)spec.AtrLen;
			atrN++;
			atr = atrN <= an ? atr + (tr - atr) / atrN : (atr * (an - 1) + tr) / an;
			double af = 2.0 / (spec.Fast + 1), asl = 2.0 / (spec.Slow + 1);
			emaF = closes.Count == 1 ? c : emaF + af * (c - emaF);
			emaS = closes.Count == 1 ? c : emaS + asl * (c - emaS);
			if (!double.IsNaN(prevClose))
			{
				int rn = Math.Max(2, (int)spec.Len);
				double g = Math.Max(0, c - prevClose), lo = Math.Max(0, prevClose - c);
				rsiN++;
				if (rsiN <= rn) { avgGain += (g - avgGain) / rsiN; avgLoss += (lo - avgLoss) / rsiN; }
				else { avgGain = (avgGain * (rn - 1) + g) / rn; avgLoss = (avgLoss * (rn - 1) + lo) / rn; }
				rsi = avgLoss == 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
			}
		}

		private static double Mean(List<double> a, int from, int to) { double s = 0; for (int i = from; i < to; i++) s += a[i]; return s / Math.Max(1, to - from); }
		private static double Std(List<double> a, int from, int to, double m) { double s = 0; for (int i = from; i < to; i++) s += (a[i] - m) * (a[i] - m); return Math.Sqrt(s / Math.Max(1, to - from)); }
		private static double Max(List<double> a, int from, int to) { double r = double.MinValue; for (int i = Math.Max(0, from); i < to; i++) r = Math.Max(r, a[i]); return r; }
		private static double Min(List<double> a, int from, int to) { double r = double.MaxValue; for (int i = Math.Max(0, from); i < to; i++) r = Math.Min(r, a[i]); return r; }

		// ------------------------------------------------------------------ spec loading
		private string Folder()
		{
			return string.IsNullOrEmpty(JobFolder) ? Path.Combine(NinjaTrader.Core.Globals.UserDataDir, "nostro") : JobFolder;
		}

		private Spec LoadSpec(string batch, int index)
		{
			string path = Path.Combine(Path.Combine(Folder(), "jobs"), batch + ".json");
			Dictionary<string, object> root = MiniJson.Parse(File.ReadAllText(path)) as Dictionary<string, object>;
			List<object> specs = root != null && root.ContainsKey("specs") ? root["specs"] as List<object> : null;
			if (specs == null || index < 0 || index >= specs.Count)
				throw new ArgumentException("NostroLab: batch " + batch + " has no spec #" + index);
			Dictionary<string, object> s = (Dictionary<string, object>)specs[index];
			Spec r = new Spec();
			r.Id = Str(s, "id", r.Id); r.Family = Str(s, "family", r.Family); r.Dir = Str(s, "dir", r.Dir);
			Dictionary<string, object> p = Obj(s, "p"), se = Obj(s, "session"), ex = Obj(s, "exit");
			r.RangeMin = Num(p, "range_min", r.RangeMin); r.BufferTicks = Num(p, "buffer_ticks", r.BufferTicks);
			r.Fast = Num(p, "fast", r.Fast); r.Slow = Num(p, "slow", r.Slow); r.TrendLen = Num(p, "trend_len", r.TrendLen);
			r.Len = Num(p, "len", r.Len); r.Lo = Num(p, "lo", r.Lo); r.Hi = Num(p, "hi", r.Hi); r.K = Num(p, "k", r.K); r.DevAtr = Num(p, "dev_atr", r.DevAtr);
			r.OpenMin = Hm(Str(se, "open", "09:30")); r.EntryFrom = Hm(Str(se, "entry_from", "09:35")); r.EntryTo = Hm(Str(se, "entry_to", "11:30")); r.Flat = Hm(Str(se, "flat", "15:55"));
			r.StopAtr = Num(ex, "stop_atr", r.StopAtr); r.TargetAtr = Num(ex, "target_atr", r.TargetAtr); r.TrailAtr = Num(ex, "trail_atr", r.TrailAtr); r.MaxBars = Num(ex, "max_bars", r.MaxBars);
			r.AtrLen = Num(s, "atr_len", r.AtrLen); r.MaxTradesDay = Num(s, "max_trades_day", r.MaxTradesDay); r.Qty = Num(s, "qty", r.Qty);
			return r;
		}

		private Spec FromProperties()
		{
			Spec r = new Spec();
			r.Id = "direct"; r.Family = Family; r.Dir = Direction;
			r.RangeMin = RangeMin; r.BufferTicks = BufferTicks; r.Fast = Fast; r.Slow = Slow; r.TrendLen = TrendLen; r.Len = Len; r.Lo = Lo; r.Hi = Hi; r.K = BandK; r.DevAtr = DevAtr;
			r.OpenMin = SessionOpen / 100 * 60 + SessionOpen % 100; r.EntryFrom = EntryFrom / 100 * 60 + EntryFrom % 100;
			r.EntryTo = EntryTo / 100 * 60 + EntryTo % 100; r.Flat = FlatTime / 100 * 60 + FlatTime % 100;
			r.StopAtr = StopAtr; r.TargetAtr = TargetAtr; r.TrailAtr = TrailAtr; r.MaxBars = MaxBars; r.AtrLen = AtrLen; r.MaxTradesDay = MaxTradesDay; r.Qty = Qty;
			return r;
		}

		private static Dictionary<string, object> Obj(Dictionary<string, object> d, string k) { object v; return d != null && d.TryGetValue(k, out v) && v is Dictionary<string, object> ? (Dictionary<string, object>)v : new Dictionary<string, object>(); }
		private static string Str(Dictionary<string, object> d, string k, string def) { object v; return d != null && d.TryGetValue(k, out v) && v is string ? (string)v : def; }
		private static double Num(Dictionary<string, object> d, string k, double def) { object v; return d != null && d.TryGetValue(k, out v) && v is double ? (double)v : def; }
		private static int Hm(string s) { string[] a = s.Split(':'); return int.Parse(a[0], CultureInfo.InvariantCulture) * 60 + (a.Length > 1 ? int.Parse(a[1], CultureInfo.InvariantCulture) : 0); }
		private static TimeZoneInfo FindZone(string id) { try { return TimeZoneInfo.FindSystemTimeZoneById(id); } catch { return TimeZoneInfo.Local; } }

		// ------------------------------------------------------------------ results
		private void WriteResults()
		{
			CultureInfo ci = CultureInfo.InvariantCulture;
			StringBuilder sb = new StringBuilder();
			sb.Append("{\"batch\":").Append(Q(BatchId)).Append(",\"index\":").Append(JobIndex).Append(",\"spec_id\":").Append(Q(spec.Id));
			sb.Append(",\"engine\":\"ninjatrader\",\"instrument\":").Append(Q(Instrument.FullName)).Append(",\"bars\":").Append(CurrentBar + 1);
			sb.Append(",\"period\":").Append(Q(BarsPeriod.ToString())).Append(",\"first\":").Append(Q(firstBar.ToString("s", ci))).Append(",\"last\":").Append(Q(lastBar.ToString("s", ci)));
			sb.Append(",\"point_value\":").Append(Instrument.MasterInstrument.PointValue.ToString(ci)).Append(",\"tick\":").Append(TickSize.ToString(ci));
			sb.Append(",\"trades\":[");
			bool first = true;
			foreach (Trade t in SystemPerformance.AllTrades)
			{
				if (!first) sb.Append(',');
				first = false;
				DateTime et = TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t.Entry.Time, DateTimeKind.Unspecified), ntZone, nyZone);
				DateTime xt = TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t.Exit.Time, DateTimeKind.Unspecified), ntZone, nyZone);
				sb.Append("{\"et\":").Append(Q(et.ToString("s", ci))).Append(",\"xt\":").Append(Q(xt.ToString("s", ci)));
				sb.Append(",\"side\":").Append(Q(t.Entry.MarketPosition == MarketPosition.Long ? "long" : "short"));
				sb.Append(",\"ep\":").Append(t.Entry.Price.ToString(ci)).Append(",\"xp\":").Append(t.Exit.Price.ToString(ci));
				sb.Append(",\"qty\":").Append(t.Quantity).Append(",\"pnl\":").Append(t.ProfitCurrency.ToString(ci)).Append(",\"comm\":").Append(t.Commission.ToString(ci));
				sb.Append(",\"mae\":").Append(t.MaeCurrency.ToString(ci)).Append(",\"mfe\":").Append(t.MfeCurrency.ToString(ci)).Append(",\"exit\":").Append(Q(t.Exit.Name)).Append('}');
			}
			sb.Append("]}");
			string dir = Path.Combine(Path.Combine(Folder(), "results"), BatchId);
			Directory.CreateDirectory(dir);
			string path = Path.Combine(dir, JobIndex + ".json"), tmp = path + ".tmp";
			File.WriteAllText(tmp, sb.ToString());
			if (File.Exists(path)) File.Delete(path);
			File.Move(tmp, path);
		}

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

		// ------------------------------------------------------------------ a tiny JSON reader (no extra assemblies needed)
		private static class MiniJson
		{
			public static object Parse(string s) { int i = 0; object v = Val(s, ref i); return v; }
			private static void Ws(string s, ref int i) { while (i < s.Length && char.IsWhiteSpace(s[i])) i++; }
			private static object Val(string s, ref int i)
			{
				Ws(s, ref i);
				char c = s[i];
				if (c == '{')
				{
					Dictionary<string, object> d = new Dictionary<string, object>(); i++; Ws(s, ref i);
					if (s[i] == '}') { i++; return d; }
					while (true) { Ws(s, ref i); string k = (string)Val(s, ref i); Ws(s, ref i); i++; d[k] = Val(s, ref i); Ws(s, ref i); if (s[i] == ',') { i++; continue; } i++; return d; }
				}
				if (c == '[')
				{
					List<object> a = new List<object>(); i++; Ws(s, ref i);
					if (s[i] == ']') { i++; return a; }
					while (true) { a.Add(Val(s, ref i)); Ws(s, ref i); if (s[i] == ',') { i++; continue; } i++; return a; }
				}
				if (c == '"')
				{
					StringBuilder b = new StringBuilder(); i++;
					while (s[i] != '"')
					{
						if (s[i] == '\\')
						{
							i++; char e = s[i];
							if (e == 'u') { b.Append((char)Convert.ToInt32(s.Substring(i + 1, 4), 16)); i += 4; }
							else b.Append(e == 'n' ? '\n' : e == 't' ? '\t' : e == 'r' ? '\r' : e == 'b' ? '\b' : e == 'f' ? '\f' : e);
						}
						else b.Append(s[i]);
						i++;
					}
					i++; return b.ToString();
				}
				if (s.Substring(i).StartsWith("true")) { i += 4; return true; }
				if (s.Substring(i).StartsWith("false")) { i += 5; return false; }
				if (s.Substring(i).StartsWith("null")) { i += 4; return null; }
				int st = i;
				while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
				return double.Parse(s.Substring(st, i - st), CultureInfo.InvariantCulture);
			}
		}

		#region Properties
		[NinjaScriptProperty, Display(Name = "BatchId", Description = "NT LAB batch to run (empty = use the parameters below)", Order = 1, GroupName = "NT LAB")]
		public string BatchId { get; set; }
		[NinjaScriptProperty, Range(0, int.MaxValue), Display(Name = "JobIndex", Description = "Which spec of the batch (optimize 0..N-1)", Order = 2, GroupName = "NT LAB")]
		public int JobIndex { get; set; }
		[NinjaScriptProperty, Display(Name = "JobFolder", Description = "Empty = Documents\\NinjaTrader 8\\nostro", Order = 3, GroupName = "NT LAB")]
		public string JobFolder { get; set; }
		[NinjaScriptProperty, Display(Name = "NtTimeZoneId", Description = "NinjaTrader's time zone if it is not the PC's (e.g. 'Israel Standard Time')", Order = 4, GroupName = "NT LAB")]
		public string NtTimeZoneId { get; set; }

		[NinjaScriptProperty, Display(Name = "Family", Description = "orb, ema_cross, donchian, rsi_revert, bb_revert, vwap_revert", Order = 10, GroupName = "Rules")]
		public string Family { get; set; }
		[NinjaScriptProperty, Display(Name = "Direction", Description = "both, long, short", Order = 11, GroupName = "Rules")]
		public string Direction { get; set; }
		[NinjaScriptProperty, Display(Name = "RangeMin", Order = 12, GroupName = "Rules")] public double RangeMin { get; set; }
		[NinjaScriptProperty, Display(Name = "BufferTicks", Order = 13, GroupName = "Rules")] public double BufferTicks { get; set; }
		[NinjaScriptProperty, Display(Name = "Fast", Order = 14, GroupName = "Rules")] public double Fast { get; set; }
		[NinjaScriptProperty, Display(Name = "Slow", Order = 15, GroupName = "Rules")] public double Slow { get; set; }
		[NinjaScriptProperty, Display(Name = "TrendLen", Order = 16, GroupName = "Rules")] public double TrendLen { get; set; }
		[NinjaScriptProperty, Display(Name = "Len", Order = 17, GroupName = "Rules")] public double Len { get; set; }
		[NinjaScriptProperty, Display(Name = "Lo", Order = 18, GroupName = "Rules")] public double Lo { get; set; }
		[NinjaScriptProperty, Display(Name = "Hi", Order = 19, GroupName = "Rules")] public double Hi { get; set; }
		[NinjaScriptProperty, Display(Name = "BandK", Order = 20, GroupName = "Rules")] public double BandK { get; set; }
		[NinjaScriptProperty, Display(Name = "DevAtr", Order = 21, GroupName = "Rules")] public double DevAtr { get; set; }
		[NinjaScriptProperty, Display(Name = "SessionOpen (HHmm NY)", Order = 30, GroupName = "Session")] public int SessionOpen { get; set; }
		[NinjaScriptProperty, Display(Name = "EntryFrom (HHmm NY)", Order = 31, GroupName = "Session")] public int EntryFrom { get; set; }
		[NinjaScriptProperty, Display(Name = "EntryTo (HHmm NY)", Order = 32, GroupName = "Session")] public int EntryTo { get; set; }
		[NinjaScriptProperty, Display(Name = "FlatTime (HHmm NY)", Order = 33, GroupName = "Session")] public int FlatTime { get; set; }
		[NinjaScriptProperty, Display(Name = "StopAtr", Order = 40, GroupName = "Exits")] public double StopAtr { get; set; }
		[NinjaScriptProperty, Display(Name = "TargetAtr", Order = 41, GroupName = "Exits")] public double TargetAtr { get; set; }
		[NinjaScriptProperty, Display(Name = "TrailAtr", Order = 42, GroupName = "Exits")] public double TrailAtr { get; set; }
		[NinjaScriptProperty, Display(Name = "MaxBars", Order = 43, GroupName = "Exits")] public double MaxBars { get; set; }
		[NinjaScriptProperty, Display(Name = "AtrLen", Order = 44, GroupName = "Exits")] public double AtrLen { get; set; }
		[NinjaScriptProperty, Display(Name = "MaxTradesDay", Order = 45, GroupName = "Exits")] public double MaxTradesDay { get; set; }
		[NinjaScriptProperty, Display(Name = "Qty", Order = 46, GroupName = "Exits")] public double Qty { get; set; }
		#endregion
	}
}
