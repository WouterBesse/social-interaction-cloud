import copy
import threading
import time
from signal import signal, SIGTERM, SIGINT
from sys import exit

import sic_framework.core.sic_logging
from sic_framework.core.utils import isinstance_pickle

from . import utils, sic_logging
from .message_python2 import SICMessage, SICStopRequest, SICRequest, SICIgnoreRequestMessage, SICSuccessMessage
from .sic_redis import SICRedis


class SICStartComponentRequest(SICRequest):
    """
    A request from a user for this component manager, running on a device, to start a component which
    will be providing some type of capability from this device.
    """

    def __init__(self, component_name, log_level, conf=None):
        super(SICStartComponentRequest, self).__init__()
        self.component_name = component_name  # str
        self.log_level = log_level  # logging.LOGLEVEL
        self.conf = conf  # SICConfMessage


class SICStartedComponentInformation(SICMessage):
    """
    A reply to the SICStartServiceRequest, most importantly communicating the output channel that the component
    is publishing its output data to.
    """
    component_is_singleton = False

    def __init__(self, output_channel):
        self.output_channel = output_channel  # str


class SICNotStartedMessage(SICMessage):
    def __init__(self, message):
        self.message = message


class SICComponentManager(object):
    # The maximum error between the redis server and this device's clocks in seconds
    MAX_REDIS_SERVER_TIME_DIFFERENCE = 2

    # Number of seconds we wait at most for a component to start
    COMPONENT_START_TIMEOUT = 10

    def __init__(self, component_classes):
        """
        A component manager to start components when requested by users.
        :param component_classes: List of SICService components to be started
        """

        # Redis initialization
        self.redis = SICRedis()
        self.ip = utils.get_ip_adress()

        self.active_components = []
        self.component_classes = {cls.get_component_name(): cls for cls in component_classes}
        self.component_counter = 0

        self.stop_event = threading.Event()
        self.ready_event = threading.Event()

        self.logger = self.get_manager_logger()

        # The _handle_request function is calls execute directly, as we must reply when execution done to allow the user
        # to wait for this. New messages will be buffered by redis. The component manager listens to
        self.redis.register_request_handler(self.ip, self._handle_request)

        print('Starting component manager on ip "{}" with components:'.format(self.ip))
        for c in self.component_classes.values():
            print(" - {}".format(c.get_component_name()))

        # TODO FIXME
        # self._sync_time()

        self.ready_event.set()

    def serve(self):
        """
        Listen for requests until this component manager is signaled to stop running.
        """
        # wait for the signal to stop, loop is necessary for ctrl-c to work on python2
        while True:
            self.stop_event.wait(timeout=.1)
            if self.stop_event.is_set():
                break
        self.shutdown()
        print("Stopped component manager.")

    def _sync_time(self):
        """
        WORK IN PROGRESS
        clock on devices is often not correct, so we need to correct for this
        """
        # Check if the time of this device is off, because that would interfere with sensor fusion across devices
        time_diff_seconds = abs(time.time() - float("{}.{}".format(*self.redis.time())))
        if time_diff_seconds > .1:
            print("Warning: device time difference to redis server is {} seconds".format(time_diff_seconds))
            print("This is allowed (max: {}), but might cause data to fused incorrectly in components.".format(
                self.MAX_REDIS_SERVER_TIME_DIFFERENCE))
        if time_diff_seconds > self.MAX_REDIS_SERVER_TIME_DIFFERENCE:
            raise ValueError("The time on this device differs by {} seconds from the redis server (max: {}s)".format(
                time_diff_seconds, self.MAX_REDIS_SERVER_TIME_DIFFERENCE))

    def _handle_request(self, channel, request):
        """
        Start a component on this device as requested by a user. A thread is started to run the component, and component
        threads are restarted/reused when a user re-requests the component. Separated such that SICSingletonFactory can
        override this method.
        :param request: The SICStartServiceRequest request
        """

        if isinstance_pickle(request, SICStopRequest):
            self.stop_event.set()
            # return an empty stop message as a request must always be replied to
            return SICSuccessMessage()

        # reply to the request if the component manager can start the component
        if request.component_name in self.component_classes:
            print("{} handling request {}".format(self.__class__.__name__, request.component_name))

            return self.start_component(request)
        else:
            print("{} ignored request {}".format(self.__class__.__name__, request.component_name))
            return SICIgnoreRequestMessage()

    def get_manager_logger(self, log_level=sic_logging.INFO):
        """
        Create a logger to inform the user during the setup of the component by the manager.
        :return: Logger
        """
        name = "{manager}-{ip}".format(manager=self.__class__.__name__, ip=self.ip)

        logger = sic_logging.get_sic_logger(self.redis, name, log_level, sic_framework.core.sic_logging.get_log_channel())
        logger.info("Manager on device {} starting".format(self.ip))

        return logger

    def start_component(self, request):
        """
        Start a component on this device as requested by a user. A thread is started to run the component in.
        :param request: The SICStartServiceRequest request
        :param logger: The logger for any messages from the component manager
        :return: the SICStartedServiceInformation with the information to connect to the started component.
        """

        component_class = self.component_classes[request.component_name] # SICComponent

        output_channel = component_class.get_output_channel(self.ip)

        try:
            stop_event = threading.Event()
            ready_event = threading.Event()
            component = component_class(output_channel=output_channel,
                                        stop_event=stop_event,
                                        ready_event=ready_event,
                                        log_level=request.log_level,
                                        conf=request.conf,
                                        )
            thread = threading.Thread(target=component._start)
            thread.name = component_class.get_component_name()
            thread.start()

            # wait till the component is ready to receive input
            component._ready_event.wait(component.COMPONENT_STARTUP_TIMEOUT)

            if component._ready_event.is_set() is False:
                self.logger.error(
                    "Component {} refused to start within {} seconds!".format(component.get_component_name, component.COMPONENT_STARTUP_TIMEOUT))
                # Todo do something!

            # store the thread for this user under their ID, so we can keep track of all the threads and
            # restart/reuse them for each user
            self.active_components.append(component)

            # inform the user their component has started and what the output channel is
            reply = SICStartedComponentInformation(output_channel)

            return reply

        except Exception as e:
            # self.logger.exception(e) # maybe not needed if already sending back a not started message
            return SICNotStartedMessage(e)

    def shutdown(self, *args):
        print('Trying to exit manager gracefully...')
        try:
            self.redis.close()
            for component in self.active_components:
                component._stop_event.set()
            print('Graceful exit was successful')
        except Exception as err:
            print('Graceful exit has failed: {}'.format(err.message))


class SICSensorManager(SICComponentManager):
    started_components = dict()

    def start_component(self, request):
        """
        Start a component on this device as requested by a user. A thread is started to run the component in.
        :param request: The SICStartServiceRequest request
        :return: the SICStartedServiceInformation with the information to connect to the started component.
        """

        component_class = self.component_classes[request.component_name]

        if component_class.get_component_name() not in self.started_components:
            self.logger.info("Starting new component {}".format(request.component_name))

            reply = super(SICSensorManager, self).start_component(request)
            reply.component_is_singleton = True

            self.started_components[component_class.get_component_name()] = reply
        else:
            self.logger.info("Reusing existing component {}".format(component_class.get_component_name()))
            reply = self.started_components[component_class.get_component_name()]

        # send a copy, as to not have _request_id to the stored object (which would prohibit subsequent replies)
        return copy.copy(reply)