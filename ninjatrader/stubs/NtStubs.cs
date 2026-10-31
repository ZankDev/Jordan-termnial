// Compile-check stand-ins for the NinjaTrader 8 types NostroLab / NostroBridge use. NOT shipped to NinjaTrader:
// they only let `ninjatrader/check.ps1` compile the NinjaScript files with the .NET Framework compiler before NinjaTrader
// is installed. Signatures mirror the NinjaTrader 8 help guide.
using System;
using System.Collections;
using System.Collections.Generic;

namespace NinjaTrader.Core { public static class Globals { public static string UserDataDir = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments) + "\\NinjaTrader 8\\"; public static DateTime Now { get { return DateTime.Now; } } } }

namespace NinjaTrader.Cbi
{
	public enum MarketPosition { Flat, Long, Short }
	public enum ConnectionStatus { Connected, Connecting, ConnectionLost, Disconnected, Disconnecting }
	public class MasterInstrument { public double PointValue; public double TickSize; public string Name; public string Description; public InstrumentType InstrumentType; }
	public enum InstrumentType { Future, Stock, Forex, Index, Option, Cfd, CryptoCurrency }
	public class Instrument { public string FullName; public MasterInstrument MasterInstrument = new MasterInstrument(); public static Instrument GetInstrument(string name) { return null; } }
	public class Execution { public DateTime Time; public double Price; public MarketPosition MarketPosition; public string Name; }
	public class Trade { public Execution Entry = new Execution(), Exit = new Execution(); public int Quantity; public double ProfitCurrency, Commission, MaeCurrency, MfeCurrency; }
	public class TradeCollection : IEnumerable<Trade> { private List<Trade> l = new List<Trade>(); public int Count { get { return l.Count; } } public IEnumerator<Trade> GetEnumerator() { return l.GetEnumerator(); } IEnumerator IEnumerable.GetEnumerator() { return l.GetEnumerator(); } }
	public class SystemPerformance { public TradeCollection AllTrades = new TradeCollection(); }
	public class Position { public MarketPosition MarketPosition; public int Quantity; }
	public class ConnectionOptions { public string Name; }
	public class Connection { public ConnectionStatus Status; public ConnectionOptions Options = new ConnectionOptions(); public static List<Connection> Connections = new List<Connection>(); }
	public class Account { public string Name; public static List<Account> All = new List<Account>(); }
}

namespace NinjaTrader.Data
{
	public enum BarsPeriodType { Tick, Volume, Second, Minute, Day, Week, Month, Year, Range }
	public class BarsPeriod { public BarsPeriodType BarsPeriodType; public int Value; public override string ToString() { return Value + " " + BarsPeriodType; } }
	public class TradingHours { public static TradingHours Get(string name) { return new TradingHours(); } public string Name; }
	public enum ErrorCode { NoError, UserAbort, Panic, NoData }
	public class Bars
	{
		public int Count;
		public DateTime GetTime(int i) { return DateTime.MinValue; }
		public double GetOpen(int i) { return 0; } public double GetHigh(int i) { return 0; } public double GetLow(int i) { return 0; } public double GetClose(int i) { return 0; } public long GetVolume(int i) { return 0; }
		public bool IsFirstBarOfSession;
	}
	public class BarsRequest : IDisposable
	{
		public BarsRequest(NinjaTrader.Cbi.Instrument instrument, DateTime from, DateTime to) { }
		public BarsRequest(NinjaTrader.Cbi.Instrument instrument, int barsBack) { }
		public BarsPeriod BarsPeriod; public TradingHours TradingHours; public Bars Bars;
		public void Request(Action<BarsRequest, ErrorCode, string> callback) { }
		public void Dispose() { }
	}
}

namespace NinjaTrader.NinjaScript
{
	public enum State { SetDefaults, Configure, Active, DataLoaded, Historical, Transition, Realtime, Terminated, Finalized }
	public enum Calculate { OnBarClose, OnEachTick, OnPriceChange }
	public enum EntryHandling { AllEntries, UniqueEntries }
	public enum CalculationMode { Currency, Percent, Price, Ticks, Pips }
	[AttributeUsage(AttributeTargets.Property)] public class NinjaScriptPropertyAttribute : Attribute { }
	public interface ISeries<T> { T this[int barsAgo] { get; } }
	public class Series : ISeries<double> { public double this[int barsAgo] { get { return 0; } } }
	public class TimeSeries : ISeries<DateTime> { public DateTime this[int barsAgo] { get { return DateTime.MinValue; } } }
	public class VolumeSeries : ISeries<double> { public double this[int barsAgo] { get { return 0; } } }
	public abstract class NinjaScriptBase
	{
		public string Name, Description; public State State;
		protected virtual void OnStateChange() { }
		public void Print(object o) { }
	}
	public abstract class StrategyBase : NinjaScriptBase
	{
		public Calculate Calculate; public int EntriesPerDirection; public EntryHandling EntryHandling; public bool IsExitOnSessionCloseStrategy, IsInstantiatedOnEachOptimizationIteration;
		public int ExitOnSessionCloseSeconds, BarsRequiredToTrade, CurrentBar, BarsInProgress; public double TickSize;
		public Series Open = new Series(), High = new Series(), Low = new Series(), Close = new Series(); public VolumeSeries Volume = new VolumeSeries(); public TimeSeries Time = new TimeSeries();
		public NinjaTrader.Cbi.Position Position = new NinjaTrader.Cbi.Position(); public NinjaTrader.Cbi.Instrument Instrument = new NinjaTrader.Cbi.Instrument();
		public NinjaTrader.Cbi.SystemPerformance SystemPerformance = new NinjaTrader.Cbi.SystemPerformance(); public NinjaTrader.Data.BarsPeriod BarsPeriod = new NinjaTrader.Data.BarsPeriod();
		protected virtual void OnBarUpdate() { }
		public object EnterLong(int qty, string signal) { return null; } public object EnterShort(int qty, string signal) { return null; }
		public object ExitLong(string signal, string fromEntry) { return null; } public object ExitShort(string signal, string fromEntry) { return null; }
		public void SetStopLoss(string fromEntry, CalculationMode mode, double value, bool simulated) { }
		public void SetTrailStop(string fromEntry, CalculationMode mode, double value, bool simulated) { }
		public void SetProfitTarget(string fromEntry, CalculationMode mode, double value) { }
	}
	public abstract class AddOnBase : NinjaScriptBase { }
}
namespace NinjaTrader.NinjaScript.Strategies { public abstract class Strategy : NinjaTrader.NinjaScript.StrategyBase { } }
namespace NinjaTrader.NinjaScript.AddOns { }
