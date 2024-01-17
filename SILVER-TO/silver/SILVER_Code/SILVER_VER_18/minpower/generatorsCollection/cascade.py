from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from ..commonscripts import update_attributes
from SILVER_VER_18.config_variables import *
from ..schedule import is_init
from .. import bidding
from .baseGenerator import BaseGenerator


class Cascade(BaseGenerator):

    """
    A Hydro Cascade model

    :param nh_input: #reservoir inputs from non-hydro sources
    :param precip: #precipitation
    :param s_0: #initial storage volume
    :param s_min: #minimum storage volume
    :param s_max: #maximum storage volume
    :param d_min: #minimum discharge rate (replace with pmin??)
    :param d_max: #maximum discharge rate (replace with pmax??)
    :param h_factor: #ratio of head (dam height) to storage volume
    :param evap: #evaporation from reservoirs
    :param eff: #generator efficiency
    :param cap: #capacity of generator (MW) (replace with pmax??)
    :param price: #electricity price

    :param pmin: minimum real power
    :param pmax: maximum real power
    :param minuptime: min. time after commitment in on status (hours)
    :param mindowntime: min. time after de-commitment in off status (hours)
    :param rampratemax: max. positive change in real power over 1hr (MW/hr)
    :param rampratemin: max. negative change in real power over 1hr (MW/hr)
    :param startupramplimit: max. positive change in real power over the
        first hour after startup (MW/hr)
    :param shutdownramplimit: max. negative change in real power over the
        last hour before shutdown (MW/hr)
    :param costcurveequation: text describing a polynomial cost curve ($/MWh)
      see :meth:`~bidding.parsePolynomial` for more.
    :param heatratestring: text describing a polynomial heat rate curve (MBTU/MW).
        converts to cost curve when multiplied by fuelcost.
    :param fuelcost: cost of fuel ($/MBTU)
    :param startupcost: cost to commit ($)
    :param shutdowncost: cost to de-commit ($)
    :param mustrun: flag that forces commimtent to be on

    :param name: name of the generator
    :param index: numbering of the generator
    :param bus: bus name that the generator is connected to
    """

    def __init__(self, kind='generic',
                 pmin=0, pmax=500,
                 minuptime=0, mindowntime=0,
                 rampratemax=None, rampratemin=None,
                 costcurveequation='20P',
                 heatrateequation=None, fuelcost=1,
                 bid_points=None,
                 noloadcost=0,
                 startupcost=0, shutdowncost=0,
                 startupramplimit=None,
                 shutdownramplimit=None,
                 faststart=False,
                 mustrun=False,
                 name='', index=None, bus=None,
                 maxdailyenergy=0,
                 cascadegroupname=None,
                 number=None,
                 minstorage=None,
                 maxstorage=None,
                 minwaterdischarge=None,
                 maxwaterdischarge=None,
                 hfactor=None,
                 efficiency=None,
                 nhinput=None
                 ):

        update_attributes(self, locals())  # load in inputs

        # The formulation below requires that startup ramp limits are set.
        # The defaults are the normal ramp rate limits,
        # unless Pmin so high and Rmin/max small that startup is infeasible.
        # In that case the default is Pmin.

        if (self.startupramplimit is None) and (self.rampratemax is not None):
            self.startupramplimit = max(self.pmin, self.rampratemax)

        if (self.shutdownramplimit is None) and (self.rampratemin is not None):
            self.shutdownramplimit = min(-1 * self.pmin, self.rampratemin)

        self.fuelcost = float(fuelcost)
        self.is_controllable = True
        self.is_stochastic = False
        self.commitment_problem = True
        self.build_cost_model()
        self.init_optimization()

    ####################################
    # OBJECTIVE FUNCTION
    ####################################
    def spinning_cost(self, time=None, scenario=None):
        self.spinning_cost_value = 100
        return self.spinning_cost_value * self.status(time, scenario)

    ###############################################
    ################# Variables   #################
    ###############################################

    def create_variables(self, times):
        '''
        Create the optimization variables for a generator over all times.
        Also create the :class:`bidding.Bid` objects and their variables.
        '''

        self.commitment_problem = len(times) > 1
        self.add_variable('discharge', index=times.set, low=self.minwaterdischarge, high=self.maxwaterdischarge)
        self.add_variable('spill', index=times.set, low=0, high=None)
        self.add_variable('storage_level', index=times.set, low=self.minstorage, high=self.maxstorage)

        if self.commitment_problem or user_config.dispatch_decommit_allowed:
            self.add_variable('status', index=times.set, kind='Binary',
                              fixed_value=1 if self.mustrun else None)

        if self.commitment_problem:
            # power_available exists for easier reserve requirement
            self.reserve_required = self._parent_problem().reserve_required
            if self.reserve_required:
                self.add_variable(
                    'power_available', index=times.set, low=0, high=self.pmax)
            if self.startupcost > 0:
                self.add_variable('startupcost', index=times.set,
                                  low=0, high=self.startupcost)
            if self.shutdowncost > 0:
                self.add_variable('shutdowncost', index=times.set,
                                  low=0, high=self.shutdowncost)

        self.bids = bidding.Bid(times=times, **self.bid_params)
        return

    ############## Related Functions to define Variables ##################

    def power(self, time=None, scenario=None):

        return 997 * 9.81 * self.discharge(time) * self.hfactor * self.efficiency * (2.77e-10)

    def storage_level(self, time=None, scenario=None):

        if time is not None and is_init(time):
            return self.initial_storagecontent
        else:
            return self.get_variable('storage_level', time, scenario=scenario, indexed=True)

    def discharge(self, time=None, scenario=None):
        '''real power output (generating) at time and reservoir'''
        if time is not None and is_init(time):
            return 0
        else:
            return self.get_variable('discharge', time, scenario=scenario, indexed=True)

    def spill(self, time=None, scenario=None):
        ''' spill rate '''
        if time is not None and is_init(time):
            return 0
        else:
            return self.get_variable('spill', time, scenario=scenario, indexed=True)

    def power_available(self, time=None, scenario=None):
        '''power availble (constrained by pmax, ramprate, ...) at time'''
        if time is not None and is_init(time):
            return self.initial_power
        var_name = 'power_available' if self.commitment_problem \
            and self.reserve_required else 'discharge'
        return self.get_variable(var_name, time, scenario=scenario, indexed=True) * 997 * 9.81 * self.hfactor * self.efficiency * (2.77e-10)

    #####################################
    # CONSTRAINTS (and related functions)
    #####################################

    def create_constraints(self, times):
        '''create the optimization constraints for a generator over all times'''
        for t in times:
            # print "add constraint 1"
            self.add_constraint('max gen power 2', t, self.power(t) <= self.pmax)

        if self.commitment_problem:
            # set initial and final time constraints

            tInitial = times.initialTimestr
            tEnd = len(times)

            self.min_up_down_time_constraints(times, tInitial, tEnd)

            self.rampRateLimitConstraints(times, tInitial)

            self.reserveConstraints(times)

            ''' startup and shutdown costs'''
            if self.startupcost > 0:
                def startupcostmin(model, t):
                    tPrev = self.get_tPrev(t, model, times)

                    return self.cost_startup(t) >= self.startupcost * (
                        self.status(t) - self.status(tPrev))

                self.add_constraint_set('startup cost min', times.set, startupcostmin)

            if self.shutdowncost > 0:
                def shutdowncost(model, t):
                    tPrev = self.get_tPrev(t, model, times)
                    return self.cost_shutdown(t) >= self.shutdowncost * -1 * (self.status(t) - self.status(tPrev))

                self.add_constraint_set('shutdown cost', times.set, shutdowncost)

            # Min storage constraint

            def min_storage_rule(model, t):
                return self.minstorage <= self.storage_level(t)
            self.add_constraint_set('min_storage', times.set, min_storage_rule)

            # Maximum storage constraint
            def max_storage_rule(model, t):
                return self.maxstorage >= self.storage_level(t)
            self.add_constraint_set('max_storage', times.set, max_storage_rule)

            # Maximum discharge constraint
            def max_water_rule(model, t):
                return self.discharge(t) <= self.maxwaterdischarge
            self.add_constraint_set('max_water', times.set, max_water_rule)

            # Minimum discharge constraint
            def min_water_rule(model, t):
                return self.minwaterdischarge <= self.discharge(t)
            self.add_constraint_set('min_water', times.set, min_water_rule)
            # Boundary condition: initial storage = final storage

            self.add_constraint('boundary', tEnd, self.storage_level(times[tEnd - 1]) == self.initial_storagecontent)

        # min/max power limits
        # these always apply (even if not a UC problem)
        if self.pmin > 0:
            def min_power(model, t):
                return self.power(t) >= self.status(t) * self.pmin

            self.add_constraint_set('min gen power', times.set, min_power)

        def max_power(model, t):
            return self.power_available(t) <= self.status(t) * self.pmax
        self.add_constraint_set('max gen power', times.set, max_power)

        return

    ####################################
    # ADDITIONAL FUNCTIONS
    ####################################

    def pmax(self):
        return self.pmax

    def set_initial_condition(self, power=None,
                              status=True, hoursinstatus=100, storagecontent=None):

        self.initial_storagecontent = storagecontent

        super().set_initial_condition(power, status, hoursinstatus)
