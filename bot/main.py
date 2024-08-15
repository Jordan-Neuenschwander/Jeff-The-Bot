from typing import Optional

from cython_extensions import cy_closest_to, cy_center, cy_in_attack_range

from ares import AresBot
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, StutterGroupBack
from ares.behaviors.combat.individual import StutterUnitBack, AMove, StutterUnitForward, AttackTarget, PathUnitToTarget
from ares.consts import UnitRole, UnitTreeQueryType
from ares.behaviors.macro import Mining, BuildStructure, RestorePower

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.position import Point2
from sc2.units import Units
from sc2.unit import Unit


class JeffTheBot(AresBot):
    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)
        self.proxy_pylon: Unit = None

    async def on_step(self, iteration: int) -> None:
        await super(JeffTheBot, self).on_step(iteration)

        self.register_behavior(Mining(workers_per_gas=(max(0, int((self.supply_workers - 16) // 2)))))

        if (self.time > 3 * 60
                and self.supply_left < 8
                and self.already_pending(UnitTypeId.PYLON) == 0):

            await self.build(UnitTypeId.PYLON, near=self.start_location)

        # Build Probes Until 1 Base Saturated and one worker to build proxy (23 Workers)
        if ((self.supply_workers + self.already_pending(UnitTypeId.PROBE) < 23
                and self.can_afford(UnitTypeId.PROBE)
                and len(self.townhalls) > 0)
                and self.townhalls.first.is_idle):

            self.townhalls.first.train(UnitTypeId.PROBE)

        # Start Warpgate Research
        if (not self.pending_or_complete_upgrade(UpgradeId.WARPGATERESEARCH)
                and self.structures(UnitTypeId.CYBERNETICSCORE).ready
                and self.can_afford(UpgradeId.WARPGATERESEARCH)):
            self.structures(UnitTypeId.CYBERNETICSCORE).first.research(UpgradeId.WARPGATERESEARCH)

        # Chrono Warpgate Research and warpgates afterwards
        if len(self.townhalls) > 0 and self.townhalls.first.energy >= 50:
            if (self.structures(UnitTypeId.CYBERNETICSCORE).ready
                    and not self.structures(UnitTypeId.CYBERNETICSCORE).first.is_idle
                    and BuffId.CHRONOBOOSTENERGYCOST not in self.structures(UnitTypeId.CYBERNETICSCORE).first.buffs
                    and self.time < 2 * 60 + 30):

                self.townhalls.first(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST,
                                     self.structures(UnitTypeId.CYBERNETICSCORE).first)

            elif UpgradeId.WARPGATERESEARCH in self.state.upgrades:
                for gate in self.structures(UnitTypeId.WARPGATE):
                    abilities = await self.get_available_abilities(gate)
                    if (AbilityId.WARPGATETRAIN_STALKER not in abilities
                            and BuffId.CHRONOBOOSTENERGYCOST not in gate.buffs):

                        self.townhalls.first(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, gate)

        # Produce Stalkers
        if not self.structures(UnitTypeId.WARPGATE).empty:
            for gate in self.structures(UnitTypeId.WARPGATE):
                abilities = await self.get_available_abilities(gate)
                if (AbilityId.WARPGATETRAIN_STALKER in abilities
                        and self.can_afford(UnitTypeId.STALKER)):
                    if self.proxy_pylon is not None:
                        position = self.mediator.get_enemy_third
                    elif not self.townhalls.empty:
                        position = self.townhalls.first.position
                    else:
                        position = self.start_location

                    gate.warp_in(UnitTypeId.STALKER, await self.find_placement(AbilityId.WARPGATETRAIN_STALKER, position))

        elif UpgradeId.WARPGATERESEARCH in self.state.upgrades and not self.structures(UnitTypeId.GATEWAY).empty:
            for gate in self.structures(UnitTypeId.GATEWAY):
                if gate.is_idle:
                    gate(AbilityId.MORPH_WARPGATE)

        elif (self.structures(UnitTypeId.CYBERNETICSCORE).ready
              and not self.structures(UnitTypeId.GATEWAY).empty
              and self.time < 3 * 60):
            for gate in self.structures(UnitTypeId.GATEWAY):
                if gate.is_idle:
                    gate.train(UnitTypeId.STALKER)

        # Build Proxy
        if (len(self.structures(UnitTypeId.PYLON)) < 3
                and not self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).empty
                and self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).first.is_idle):
            self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).first.build(UnitTypeId.PYLON,
                                                                                      self.mediator.get_enemy_third)
        if (len(self.structures(UnitTypeId.PYLON)) >= 3
                and len(self.structures(UnitTypeId.GATEWAY)) < 4
                and not self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).empty):

            self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).first.build(UnitTypeId.GATEWAY,
                                                                                      self.mediator.get_enemy_third.offset(
                                                                                          Point2((-2, -2))))
        enemy_units = self.enemy_units
        enemy_structures = self.enemy_structures
        stalker_group = self.mediator.get_units_from_role(role=UnitRole.ATTACKING)

        for stalker in stalker_group:
            stalker_attack = CombatManeuver()

            if not enemy_units.empty:
                enemy_in_range = cy_in_attack_range(stalker, enemy_units)
                if len(enemy_in_range) > 0:
                    target: Unit = cy_closest_to(stalker.position, enemy_in_range)
                    if target.type_id != UnitTypeId.LARVA and target.type_id != UnitTypeId.EGG:
                        stalker_attack.add(StutterUnitBack(
                            stalker,
                            target,
                            True,
                            self.mediator.get_ground_grid
                        ))

            if not enemy_structures.empty:
                target: Unit = cy_closest_to(stalker.position, enemy_structures)
                if (target.type_id != UnitTypeId.PHOTONCANNON
                        or target.type_id != UnitTypeId.SPINECRAWLER
                        or target.type_id != UnitTypeId.BUNKER
                        or target.type_id != UnitTypeId.PLANETARYFORTRESS):
                    stalker_attack.add(StutterUnitForward(stalker, target))
                else:
                    stalker_attack.add(StutterUnitBack(stalker, target))

            elif (stalker.distance_to(self.enemy_start_locations[0]) < 20
                  and len(self.mediator.get_units_from_role(role=UnitRole.SCOUTING)) < 1):

                self.mediator.assign_role(tag=stalker.tag, role=UnitRole.SCOUTING)
                for i, location in enumerate(self.expansion_locations_list):
                    stalker.move(location, queue=i != 0)

            if self.time > 3 * 60 + 30:
                stalker_attack.add(PathUnitToTarget(
                        stalker,
                        self.mediator.get_ground_grid,
                        self.enemy_start_locations[0]
                    ))

            self.register_behavior(stalker_attack)

    async def on_unit_created(self, unit: Unit) -> None:
        await super(JeffTheBot, self).on_unit_created(unit)

        # Assign the 18th worker the role of proxy
        if (unit.type_id == UnitTypeId.PROBE
                and self.supply_workers >= 18
                and self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).empty):
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.PROXY_WORKER)
            unit.move(self.mediator.get_enemy_third)

        # Assign Stalkers to Roles
        if unit.type_id == UnitTypeId.STALKER:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.ATTACKING)
            unit.move(self.mediator.get_enemy_third)

    async def on_building_construction_started(self, unit: Unit) -> None:
        await super(JeffTheBot, self).on_building_construction_started(unit)
        if (unit.type_id == UnitTypeId.PYLON
                and self.proxy_pylon is None
                and unit.distance_to(self.mediator.get_enemy_third) < 20):

            self.proxy_pylon = unit

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(JeffTheBot, self).on_unit_destroyed(unit_tag)

        if self.proxy_pylon is not None and unit_tag == self.proxy_pylon.tag:
            self.proxy_pylon = None
            if not self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).empty:
                self.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER).first.build(
                    UnitTypeId.PYLON, self.mediator.get_enemy_third
                )

    # async def on_building_construction_complete(self, unit: Unit) -> None:
    #     await super(JeffTheBot, self).on_building_construction_complete(unit)

        # custom on_building_construction_complete logic here ...

    # async def on_start(self) -> None:
    #     await super(MyBot, self).on_start()

    # on_start logic here ...

    # async def on_end(self, game_result: Result) -> None:
    #     await super(MyBot, self).on_end(game_result)
    #
    #     # custom on_end logic here ...
    #



    # async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
    #     await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)
    #
    #     # custom on_unit_took_damage logic here ...