from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from builtins import str
from builtins import range
from past.utils import old_div
import pandas as pd
import logging
from ..config import user_config
from ..commonscripts import update_attributes, bool_to_int
from SILVER_VER_18.config_variables import *
from ..optimization import value, OptimizationObject
from ..schedule import is_init
from .. import bidding


class BaseGenerator(OptimizationObject):
    '''
    A base class for a generator.
    This also serves as a template for
    how :class:`Generator`s are structured.
    '''

    def __init__(self, *args, **kwargs):
        '''
        This is meant to be a parent class to be inherited from and thus
        this __init__ should not be called directly.
        '''
        update_attributes(self, locals())  # load in inputs
        self.init_optimization()

    ####################################
    # OBJECTIVE FUNCTION AND RELATED
    ####################################
    def create_objective(self, times):
        return sum(self.cost(time) for time in times)

    def cost(self, time, scenario=None, evaluate=False):
        '''total cost at time (operating + startup + shutdown)'''
        return self.operatingcost(time, scenario, evaluate) + \
            self.cost_startup(time, scenario, evaluate) + \
            self.cost_shutdown(time, scenario, evaluate)

    def cost_startup(self, time, scenario=None, evaluate=False):
        if self.startupcost == 0 or not self.commitment_problem:
            return 0
        c = self.get_variable('startupcost', time, scenario=scenario, indexed=True)
        return value(c) if evaluate else c

    def cost_shutdown(self, time, scenario=None, evaluate=False):
        if self.shutdowncost == 0 or not self.commitment_problem:
            return 0
        c = self.get_variable('shutdowncost', time, scenario=scenario, indexed=True)
        return value(c) if evaluate else c

    def operatingcost(self, time=None, scenario=None, evaluate=False):
        '''cost of real power production at time (based on bid model approximation).'''
        return self.bids.output(time, scenario=scenario, evaluate=evaluate)

    def truecost(self, time, scenario=None):
        '''exact cost of real power production at time (based on exact bid polynomial).'''
        return value(self.status(time, scenario)) * self.bids.output_true(self.power(time, scenario))

    def incrementalcost(self, time, scenario=None):
        '''change in cost with change in power at time (based on exact bid polynomial).'''
        return self.bids.output_incremental(self.power(time, scenario)) if value(self.status(time, scenario)) else None

    def cost_first_stage(self, times):
        return sum(self.cost_startup(time) + self.cost_shutdown(time) for time in times)

    def cost_second_stage(self, times):
        return sum(self.operatingcost(time) for time in times)

    def build_cost_model(self):
        '''
        parse the coefficients for the polynomial bid curve
        or custom bid points definition
        '''

        bid_params = dict(
            owner=self,
            input_variable=self.power,
            min_input=self.pmin,
            max_input=self.pmax,
            status_variable=self.status
        )

        if self.bid_points is None:
            self.cost_breakpoints = user_config.breakpoints  # polynomial specification
            if getattr(self, 'heatrateequation', None):
                self.cost_coeffs = [self.fuelcost * coef
                                    for coef in bidding.parse_polynomial(self.heatrateequation)]
            else:
                self.cost_coeffs = bidding.parse_polynomial(
                    self.costcurveequation)

            bid_params['polynomial'] = self.cost_coeffs
            bid_params['constant_term'] = self.cost_coeffs[0]
            bid_params['num_breakpoints'] = self.cost_breakpoints

            if self.noloadcost != 0:
                raise ValueError('no load cost should be defined as part of the polynomial.')

        else:
            # do some simple validation and delay construction to bidding object
            min_power_bid = self.bid_points.power.min()
            max_power_bid = self.bid_points.power.max()

            if min_power_bid > self.pmin:  # pragma: no cover
                self.pmin = min_power_bid
                logging.warning('{g} should have a min. power bid ({mpb}) <= to its min. power limit ({mpl})'.format(g=str(self), mpb=min_power_bid, mpl=self.pmin))

            if max_power_bid < self.pmax:  # pragma: no cover
                self.pmax = max_power_bid
                logging.warning('{g} should have a max. power bid ({mpb}) >= to its max. power limit ({mpl})'.format(g=str(self), mpb=max_power_bid, mpl=self.pmax))

            bid_params['polynomial'] = None
            bid_params['bid_points'] = self.bid_points
            bid_params['constant_term'] = self.noloadcost

        self.bid_params = bid_params

    ####################################
    # OPTIMIZATION VARIABLES
    ####################################
    def power(self, time=None, scenario=None):
        '''real power output (generation) at time'''
        if time is not None and is_init(time):
            return self.initial_power
        else:
            return self.get_variable('power', time, scenario=scenario, indexed=True)

    '''
    In case of storage this is available generating power at time;
    power_available exists for easier reserve requirement
    '''
    def power_available(self, time=None, scenario=None):
        '''power availble (constrained by pmax, ramprate, ...) at time'''
        if time is not None and is_init(time):
            return self.initial_power
        var_name = 'power_available' if self.commitment_problem \
            and self.reserve_required else 'power'
        return self.get_variable(var_name, time, scenario=scenario, indexed=True)

    def status(self, time=None, scenario=None):
        ''' generating on/off status at time '''
        if self.commitment_problem or user_config.dispatch_decommit_allowed:
            if time is not None and is_init(time):
                return self.initial_status
            else:
                return self.get_variable('status', time, scenario=scenario, indexed=True)
        else:
            return 1

    #####################################
    # CONSTRAINTS (and related functions)
    #####################################
    def status_change(self, t, times):
        '''is the unit changing status between t and t-1'''
        previous_status = self.status(times[t - 1]) if t > 0 else self.initial_status
        return self.status(times[t]) - previous_status

    def get_tPrev(self, t, model, times):
        return model.times.prev(t) if t != model.times.first() else times.initialTime

    def min_up_down_time_constraints(self, times, tInitial, tEnd):

        def roundoff(n):
            m = int(n)
            if n != m:  # pragma: no cover
                raise ValueError('min up/down times must be integer number of intervals, not {}'.format(n))
            return m

        ''' minimum up and down times '''
        if self.minuptime > 0:
            up_intervals_remaining = roundoff(old_div((self.minuptime - self.initial_status_hours), times.intervalhrs))
            min_up_intervals_remaining_init = int(
                min(tEnd, up_intervals_remaining * self.initial_status))
        else:
            min_up_intervals_remaining_init = 0
        if self.mindowntime > 0:
            down_intervals_remaining = roundoff(old_div((self.mindowntime - self.initial_status_hours), times.intervalhrs))
            min_down_intervals_remaining_init = int(min(tEnd, down_intervals_remaining * (self.initial_status == 0)))
        else:
            min_down_intervals_remaining_init = 0
        if min_up_intervals_remaining_init > 0:
            self.add_constraint('minuptime', tInitial, sum([(1 - self.status(times[t])) for t in range(min_up_intervals_remaining_init)]) <= 0)
        if min_down_intervals_remaining_init > 0:
            self.add_constraint('mindowntime', tInitial, sum([self.status(times[t]) for t in range(min_down_intervals_remaining_init)]) == 0)

        min_up_intervals = roundoff(old_div(self.minuptime, times.intervalhrs))
        min_down_intervals = roundoff(old_div(self.mindowntime, times.intervalhrs))

        for t, time in enumerate(times):
            # min up time
            if t >= min_up_intervals_remaining_init and self.minuptime > 0:
                no_shut_down = list(range(t, min(tEnd, t + min_up_intervals)))
                min_up_intervals_remaining = min(tEnd - t, min_up_intervals)
                E = sum([self.status(times[s]) for s in no_shut_down]) >= min_up_intervals_remaining * self.status_change(t, times)
                self.add_constraint('min up time', time, E)
            # min down time
            if t >= min_down_intervals_remaining_init and self.mindowntime > 0:
                no_start_up = list(range(t, min(tEnd, t + min_down_intervals)))
                min_down_intervals_remaining = min(
                    tEnd - t, min_down_intervals)
                E = sum([1 - self.status(times[s]) for s in no_start_up]) >= min_down_intervals_remaining * -1 * self.status_change(t, times)
                self.add_constraint('min down time', time, E)

    def rampRateLimitConstraints(self, times, tInitial):
        ''' startup up and shutdown ramping limitations '''
        if self.rampratemax is not None:
            if self.initial_power + self.rampratemax < self.pmax:
                E = self.power(times[0]) - self.initial_power <= self.rampratemax
                self.add_constraint('ramp lim high', tInitial, E)

        if self.rampratemin is not None:
            if self.initial_power + self.rampratemin > self.pmin:
                E = self.rampratemin <= self.power(times[0]) - self.initial_power
                self.add_constraint('ramp lim low', tInitial, E)

        ''' maximum change in generation from one hour to the next: max and min ramping limits '''
        if self.rampratemax is not None:
            def ramp_max(model, t):
                tPrev = self.get_tPrev(t, model, times)
                ramp_limit = self.rampratemax * self.status(tPrev)
                if self.startupramplimit is not None:
                    ramp_limit += self.startupramplimit * (self.status(t) - self.status(tPrev))
                return self.power_available(t) - self.power(tPrev) <= ramp_limit
            self.add_constraint_set('ramp limit high', times.set, ramp_max)

        if self.rampratemin is not None:
            def ramp_min(model, t):
                tPrev = self.get_tPrev(t, model, times)
                ramp_limit = self.rampratemin * self.status(t)
                if self.shutdownramplimit is not None:
                    ramp_limit += self.shutdownramplimit * (-1 * (self.status(t) - self.status(tPrev)))
                return ramp_limit <= self.power_available(t) - self.power(tPrev)
            self.add_constraint_set('ramp limit low', times.set, ramp_min)

    def reserveConstraints(self, times):
        ''' reserve constraints '''
        if self.reserve_required:
            def reserve_req(model, t):
                return self.power(t) <= self.power_available(t)

            self.add_constraint_set('max gen power avail', times.set, reserve_req)

    ####################################
    # ADDITIONAL FUNCTIONS
    ####################################
    def __str__(self):
        return 'g{ind}'.format(ind=self.index)

    def getstatus(self, tend, times, status):
        return dict(
            status=value(self.status(tend)),
            power=value(self.power(tend)),
            hoursinstatus=self.gethrsinstatus(times, status))

    def set_initial_condition(self, power=None,
                              status=True, hoursinstatus=100):
        if power is None:
            # set default power as mean output
            power = old_div((self.pmax - self.pmin), 2)
        if pd.isnull(power):
            raise ValueError('inital power cannot be null')
        self.initial_status = bool_to_int(status)
        self.initial_power = float(power * self.initial_status)  # note: this eliminates ambiguity of off status with power non-zero output
        self.initial_status_hours = hoursinstatus

    def gethrsinstatus(self, times, stat):
        if not self.is_controllable:
            return 0
        end_status = stat.ix[stat.index[-1]]

        if (stat == end_status).all():
            intervals = len(stat)
            hrs = intervals * times.intervalhrs
            if self.initial_status == end_status:
                hrs += self.initial_status_hours
        else:
            noneq = stat[stat != end_status]
            intervals = 0 if len(noneq) == 0 else len(stat.ix[noneq.index[-1]:]) - 1
            hrs = intervals * times.intervalhrs

        return hrs
