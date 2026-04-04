using System.Collections.Generic;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;

namespace MegaCrit.Sts2.Core.Models.Powers;

public sealed class GraspPower : PowerModel
{
	private class Data
	{
		public readonly Dictionary<Player, int> cardsPlayedThisTurn = new Dictionary<Player, int>();
	}

	public override PowerType Type => PowerType.Buff;

	public override PowerStackType StackType => PowerStackType.Single;

	protected override object InitInternalData()
	{
		return new Data();
	}

	public override bool TryModifyEnergyCostInCombat(CardModel card, decimal originalCost, out decimal modifiedCost)
	{
		modifiedCost = originalCost;
		bool flag;
		switch (card.Pile?.Type)
		{
		case PileType.Hand:
		case PileType.Play:
			flag = true;
			break;
		default:
			flag = false;
			break;
		}
		if (!flag)
		{
			return false;
		}
		Player owner = card.Owner;
		Dictionary<Player, int> cardsPlayedThisTurn = GetInternalData<Data>().cardsPlayedThisTurn;
		cardsPlayedThisTurn.TryGetValue(owner, out var value);
		if (value > 0)
		{
			return false;
		}
		modifiedCost = originalCost + 1m;
		return true;
	}

	public override Task BeforeCardPlayed(CardPlay cardPlay)
	{
		if (cardPlay != null && cardPlay.IsAutoPlay && !cardPlay.IsLastInSeries)
		{
			return Task.CompletedTask;
		}
		Player owner = cardPlay.Card.Owner;
		Dictionary<Player, int> cardsPlayedThisTurn = GetInternalData<Data>().cardsPlayedThisTurn;
		cardsPlayedThisTurn.TryGetValue(owner, out var value);
		cardsPlayedThisTurn[owner] = value + 1;
		return Task.CompletedTask;
	}

	public override Task BeforeSideTurnStart(PlayerChoiceContext choiceContext, CombatSide side, CombatState combatState)
	{
		if (side != CombatSide.Player)
		{
			return Task.CompletedTask;
		}
		GetInternalData<Data>().cardsPlayedThisTurn.Clear();
		return Task.CompletedTask;
	}
}
