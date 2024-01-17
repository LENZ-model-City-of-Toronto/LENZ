from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from builtins import str
from builtins import zip
from builtins import range
from ..config import user_config
from ..commonscripts import update_attributes
from SILVER_VER_18.config_variables import *
from ..optimization import value
from .. import bidding
from .baseGenerator import BaseGenerator


class Generator(BaseGenerator):

    """
    A generator model.

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

    def __init__(self,
                 kind='generic',
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
                 max_hourly_energy=None,
                 max_daily_energy=None,
                 max_monthly_energy=None
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

    def pmax(self):
        return self.pmax

    # Add in a cost for having the unit spinning (i.e. having a status of 1)
    def spinning_cost(self, time=None, scenario=None):
        self.spinning_cost_value = 100
        return self.spinning_cost_value * self.status(time, scenario)

    def set_initial_condition(self, power=None,
                              status=True, hoursinstatus=100):

        super().set_initial_condition(power, status, hoursinstatus)

    def create_variables(self, times):
        '''
        Create the optimization variables for a generator over all times.
        Also create the :class:`bidding.Bid` objects and their variables.
        '''

        self.commitment_problem = len(times) > 1
        self.add_variable('power', index=times.set, low=0, high=self.pmax)

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

    def create_constraints(self, times):
        '''create the optimization constraints for a generator over all times'''
        for t in times:
            self.add_constraint('max gen power 2', t, self.power(t) <= self.pmax)

        if self.commitment_problem:
            # set initial and final time constraints

            tInitial = times.initialTimestr
            tEnd = len(times)

            self.min_up_down_time_constraints(times, tInitial, tEnd)

            self.rampRateLimitConstraints(times, tInitial)

            self.reserveConstraints(times)

            ''' start up and shut down costs'''
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

            ###################################
            # Adding in hourly generation constraint for hydro generators
            ###################################
            if (self.kind == 'hydro_hourly'):
                def hourly_gen_constraint(model, t):
                    return self.power(t) <= self.max_hourly_energy.ix[t]

                self.add_constraint_set('max hourly generation', times.set, hourly_gen_constraint)

            ############################################################
            # Adding in daily generation constraint for hydro generators
            ############################################################
            if (self.kind == 'hydro_daily'):

                for i in range(1, NUMBER_OF_DAYS + 1):

                    def max_daily(model, t):
                        return sum(self.power(t) for t in times[24 * (i - 1):(23 + (24 * (i - 1)))]) <= self.max_daily_energy.ix['day_' + str(i)]

                    self.add_constraint_set('max_daily_' + str(i), times.set, max_daily)

            ##############################################################
            # Adding in monthly generation constraint for hydro generators
            ##############################################################

            if (self.kind == 'hydro_monthly'):
                def monthly_gen_constraint(model, t):
                    return sum(self.power(t) for t in times) <= self.max_monthly_energy.ix[0]

                self.add_constraint_set('max monthly generation', times.set, monthly_gen_constraint)

            ###################################

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


class Generator_nonControllable(Generator):

    """
    A generator with a fixed schedule.
    """

    def __init__(self,
                 schedule=None,
                 fuelcost=1,
                 costcurveequation='0',
                 bid_points=None,
                 noloadcost=0,
                 mustrun=False,
                 faststart=False,
                 sheddingallowed=False,
                 pmin=0, pmax=None,
                 name='', index=None, bus=None, kind='wind',
                 observed_values=None,
                 **kwargs):

        update_attributes(self, locals())  # load in inputs
        self.is_controllable = False
        self.startupcost = 0
        self.shutdowncost = 0
        self.fuelcost = float(fuelcost)
        self.build_cost_model()
        self.init_optimization()
        self.is_stochastic = False
        self.shedding_mode = sheddingallowed

    def power(self, time, scenario=None):
        if self.kind != 'importexport':
            if self.shedding_mode:
                return self.get_variable('power_used', time,
                                         scenario=scenario, indexed=True)
            else:
                return self.power_available(time)
        else:
            return self.power_available(time)

    def status(self, time=None, scenarios=None):
        return True

    def power_available(self, time=None, scenario=None):
        return self.get_parameter('power', time, indexed=True)

    def shed(self, time, scenario=None, evaluate=False):
        Pused = self.power(time, scenario=scenario)
        Pavail = self.power_available(time, scenario=scenario)
        if evaluate:
            Pused = value(Pused)
            Pavail = value(Pavail)
        return Pavail - Pused

    def set_initial_condition(self, power=None, status=None, hoursinstatus=None):
        self.initial_power = 0
        self.initial_status = 1
        self.initial_status_hours = 0

    def getstatus(self, tend, times=None, status=None):
        return dict(
            status=1,
            power=self.power(tend),
            hoursinstatus=0)

    def create_variables(self, times):
        if self.shedding_mode:
            self.create_variables_shedding(times)

        self.add_parameter('power', index=times.set,
                           values=dict([(t, self.get_scheduled_ouput(t)) for t in times]))
        self.create_bids(times)

    def create_bids(self, times):
        self.bids = bidding.Bid(
            polynomial=self.cost_coeffs,
            owner=self,
            times=times,
            fixed_input=True,
            status_variable=lambda *args: True,
            input_variable=self.power
        )

    def create_variables_shedding(self, times):
        self.add_variable('power_used', index=times.set, low=0)

    def create_constraints(self, times):
        if self.shedding_mode:
            for time in times:
                self.add_constraint('max_power', time,
                                    self.power(time) <= self.power_available(time))

    def cost(self, time, scenario=None, evaluate=False):
        return self.operatingcost(time, scenario=scenario, evaluate=evaluate)

    def operatingcost(self, time=None, scenario=None, evaluate=False):
        return self.bids.output(time, scenario=scenario) + \
            user_config.cost_wind_shedding * self.shed(time, scenario=scenario, evaluate=evaluate)

    def truecost(self, time, scenario=None):
        return self.cost(time)

    def incrementalcost(self, time, scenario=None):
        return self.bids.output_incremental(self.power(time))

    def cost_startup(self, time, scenario=None):
        return 0

    def cost_shutdown(self, time, scenario=None):
        return 0

    def cost_first_stage(self, times):
        return 0

    def cost_second_stage(self, times):
        return sum(self.cost(time) for time in times)

    def get_scheduled_ouput(self, time):
        return float(self.schedule.ix[time])

    def set_power_to_observed(self, times):
        power = self.power_available()
        for time in times:
            power[time] = self.observed_values[time]


class Generator_Stochastic(Generator_nonControllable):

    """
    A generator with a stochastic power output.
    """

    def __init__(self,
                 scenario_values=None,
                 costcurveequation='0',
                 fuelcost=1,
                 bid_points=None, noloadcost=0,
                 mustrun=False,
                 faststart=False,
                 pmin=0, pmax=None,
                 name='', index=None, bus=None, kind='wind',
                 observed_values=None,
                 schedule=None,
                 sheddingallowed=False,
                 **kwargs):
        update_attributes(self, locals())  # load in inputs
        self.is_controllable = False

        self.is_stochastic = not \
            (user_config.perfect_solve or user_config.deterministic_solve)
        self.build_cost_model()
        self.init_optimization()
        self.startupcost = 0
        self.shutdowncost = 0
        self.fuelcost = float(fuelcost)
        self.shedding_mode = sheddingallowed and user_config.economic_wind_shed

    def power(self, time, scenario=None):
        return self.get_variable(
            'power_used' if self.shedding_mode else 'power',
            time=time, scenario=scenario, indexed=True)

    def power_available(self, time=None, scenario=None):
        return self.get_variable('power',
                                 time=time, scenario=scenario, indexed=True)

    def _get_scenario_values(self, times, s=0):
        # scenario values are structured as a pd.Panel
        # with axes: day, scenario, {prob, [hours]}
        # the panel has items which are dates
        return self.scenario_values[times.Start.date()][
            list(range(len(times)))].ix[s].dropna().values.tolist()

    def _get_scenario_probabilities(self, times):
        # if any of the scenario values are defined, we want them
        return self.scenario_values[
            times.Start.date()].dropna(how='all').probability

    def create_variables(self, times):
        if self.shedding_mode:
            self.create_variables_shedding(times)

        if self.is_stochastic:
            # initialize parameter set to first scenario value
            scenario_one = self._get_scenario_values(times, s=0)
            self.add_parameter('power', index=times.set, values=dict(list(zip(times, scenario_one))))
        else:
            # set to forecast values
            self.add_parameter('power', index=times.set,
                               values=dict([(t, self.get_scheduled_ouput(t)) for t in times]))

        self.create_bids(times)
        return
