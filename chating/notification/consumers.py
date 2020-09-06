from django.contrib.auth import get_user_model
from django.conf import settings
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.paginator import Paginator
from django.core.serializers import serialize
from channels.db import database_sync_to_async
from django.contrib.contenttypes.models import ContentType

from datetime import datetime
import json
from time import sleep
from enum import Enum


from chat.models import UnreadChatRoomMessages
from friend.models import FriendRequest, FriendList
from notification.utils import LazyNotificationEncoder
from notification.models import Notification
from notification.exceptions import NotificationClientError
from notification.constants import *

User = get_user_model()

DEFAULT_NOTIFICATION_PAGE_SIZE = 15


class NotificationConsumer(AsyncJsonWebsocketConsumer):

    """
    Passing data to and from header.html. Notifications are displayed as "drop-downs" in the nav bar.
    There is two major categories of notifications:
        1. General Notifications
            1. FriendRequest
            2. FriendList
        2. Chat Notifications
            1. UnreadChatRoomMessages
    """
    

    async def connect(self):
        """
        Called when the websocket is handshaking as part of initial connection.
        """
        print("NotificationConsumer: connect: " + str(self.scope["user"]) )
        await self.accept()


    async def disconnect(self, code):
        """
        Called when the WebSocket closes for any reason.
        """
        print("NotificationConsumer: disconnect")



    async def receive_json(self, content):
        """
        Called when we get a text frame. Channels will JSON-decode the payload
        for us and pass it as the first argument.
        """
        command = content.get("command", None)
        #print("NotificationConsumer: receive_json. Command: " + command)
        try:
            if command == "get_general_notifications":
                await self.display_progress_bar(True)
                payload = await self.get_general_notifications(content.get("page_number", None))
                if payload == None:
                    await self.general_pagination_exhausted()
                else:
                    payload = json.loads(payload)
                    await self.send_general_notifications_payload(payload['notifications'], payload['new_page_number'])
                await self.display_progress_bar(False)


            elif command == "refresh_general_notifications":
                payload = await self.refresh_general_notifications(content['oldest_timestamp'])
                if payload == None:
                    raise NotificationClientError("Something went wrong. Try refreshing the browser.")
                else:
                    payload = json.loads(payload)
                    await self.send_general_refreshed_notifications_payload(payload['notifications'])


            elif command == "get_chat_notifications":
                await self.display_progress_bar(True)
                payload = await self.get_chat_notifications(content.get("page_number", None))
                if payload == None:
                    await self.chat_pagination_exhausted()
                else:
                    payload = json.loads(payload)
                    await self.send_chat_notifications_payload(payload['notifications'], payload['new_page_number'])
                await self.display_progress_bar(False)

            

            elif command == "get_unread_general_notifications_count":
                payload = await self.get_unread_general_notification_count()
                if payload != None:
                    payload = json.loads(payload)
                    await self.send_unread_general_notification_count(payload['count'])
            

            elif command == "refresh_chat_notifications":
                try:
                    payload = await self.refresh_chat_notifications(content['oldest_timestamp'])
                    if payload == None:
                        raise NotificationClientError("Something went wrong. Try refreshing the browser.")
                    else:
                        payload = json.loads(payload)
                        await self.send_chat_refreshed_notifications_payload(payload['notifications'])
                except Exception as e:
                    print("EXCEPTION: " + str(e))
                
            

            elif command == "mark_notifications_read":
                await self.mark_notifications_read()


            elif command == "accept_friend_request":
                try:
                    notification_id = content['notification_id']
                    payload = await self.accept_friend_request(notification_id)
                    print("ACCEPT: accept_friend_request")
                    if payload == None:
                        raise NotificationClientError("Something went wrong. Try refreshing the browser.")
                    else:
                        payload = json.loads(payload)
                        await self.send_updated_friend_request_notification(payload['notification'])
                except Exception as e:
                    print("EXCEPTION: " + str(e))
                

            elif command == "decline_friend_request":
                notification_id = content['notification_id']
                payload = await self.decline_friend_request(notification_id)
                if payload == None:
                    raise NotificationClientError("Something went wrong. Try refreshing the browser.")
                else:
                    payload = json.loads(payload)
                    await self.send_updated_friend_request_notification(payload['notification'])

        except Exception as e:
            await self.display_progress_bar(False)
            # Catch any errors and send it back
            errorData = {}
            try:
                if e.code:
                    errorData['error'] = e.code
                if e.message:
                    errorData['message'] = e.message
            except:
                errorData['message'] = "An unknown error occurred"
            await self.send_json(errorData)



    async def send_updated_friend_request_notification(self, notification):
        """
        After a friend request is accepted or declined, send the updated notification to template
        payload contains 'notification' and 'response':
            1. payload['notification']
            2. payload['response']
        """
        await self.send_json(
            {
                "general_msg_type": GENERAL_MSG_TYPE_UPDATED_NOTIFICATION,
                "notification": notification,
            },
        )

    async def send_unread_general_notification_count(self, count):
        """
        Send the number of unread "general" notifications to the template
        """
        await self.send_json(
            {
                "general_msg_type": GENERAL_MSG_TYPE_GET_UNREAD_NOTIFICATIONS_COUNT,
                "count": count,
            },
        )


    async def general_pagination_exhausted(self):
        """
        Called by receive_json when pagination is exhausted for general notifications
        """
        #print("General Pagination DONE... No more notifications.")
        await self.send_json(
            {
                "general_msg_type": GENERAL_MSG_TYPE_PAGINATION_EXHAUSTED,
            },
        )

    async def send_general_notifications_payload(self, notifications, new_page_number):
        """
        Called by receive_json when ready to send a json array of the notifications
        """
        #print("NotificationConsumer: send_general_notifications_payload")
        await self.send_json(
            {
                "general_msg_type": GENERAL_MSG_TYPE_NOTIFICATIONS_PAYLOAD,
                "notifications": notifications,
                "new_page_number": new_page_number,
            },
        )


    async def send_general_refreshed_notifications_payload(self, notifications):
        """
        Called by receive_json when ready to send a json array of the notifications
        """
        #print("NotificationConsumer: send_general_refreshed_notifications_payload")
        await self.send_json(
            {
                "general_msg_type": GENERAL_MSG_TYPE_NOTIFICATIONS_REFRESH_PAYLOAD,
                "notifications": notifications,
            },
        )


    async def chat_pagination_exhausted(self):
        """
        Called by receive_json when pagination is exhausted for chat notifications
        """
        print("Chat Pagination DONE... No more notifications.")
        await self.send_json(
            {
                "chat_msg_type": CHAT_MSG_TYPE_PAGINATION_EXHAUSTED,
            },
        )

    async def send_chat_notifications_payload(self, notifications, new_page_number):
        """
        Called by receive_json when ready to send a json array of the chat notifications
        """
        #print("NotificationConsumer: send_chat_notifications_payload")
        await self.send_json(
            {
                "chat_msg_type": CHAT_MSG_TYPE_NOTIFICATIONS_PAYLOAD,
                "notifications": notifications,
                "new_page_number": new_page_number,
            },
        )

    async def send_chat_refreshed_notifications_payload(self, notifications):
        """
        Called by receive_json when ready to send a json array of the chat notifications
        """
        #print("NotificationConsumer: send_chat_refreshed_notifications_payload")
        await self.send_json(
            {
                "chat_msg_type": CHAT_MSG_TYPE_NOTIFICATIONS_REFRESH_PAYLOAD,
                "notifications": notifications,
            },
        )


    async def display_progress_bar(self, shouldDisplay):
        #print("NotificationConsumer: display_progress_bar: " + str(shouldDisplay)) 
        await self.send_json(
            {
                "progress_bar": shouldDisplay,
            },
        )

    @database_sync_to_async
    def decline_friend_request(self, notification_id):
        """
        Decline a friend request
        """
        payload = {}
        user = self.scope["user"]
        if user.is_authenticated:
            try:
                notification = Notification.objects.get(pk=notification_id)
                friend_request = notification.content_object
                # confirm this is the correct user
                if friend_request.receiver == user:
                    # accept the request and get the updated notification
                    updated_notification = friend_request.decline()

                    # return the notification associated with this FriendRequest
                    s = LazyNotificationEncoder()
                    payload['notification'] = s.serialize([updated_notification])[0]
                    return json.dumps(payload)
            except Notification.DoesNotExist:
                raise NotificationClientError("An error occurred with that notification. Try refreshing the browser.")
        return None

    @database_sync_to_async
    def accept_friend_request(self, notification_id):
        """
        Accept a friend request
        """
        payload = {}
        user = self.scope["user"]
        if user.is_authenticated:
            try:
                notification = Notification.objects.get(pk=notification_id)
                friend_request = notification.content_object
                # confirm this is the correct user
                if friend_request.receiver == user:
                    # accept the request and get the updated notification
                    updated_notification = friend_request.accept()

                    # return the notification associated with this FriendRequest
                    s = LazyNotificationEncoder()
                    payload['notification'] = s.serialize([updated_notification])[0]
                    return json.dumps(payload)
            except Notification.DoesNotExist:
                raise NotificationClientError("An error occurred with that notification. Try refreshing the browser.")
        return None


    @database_sync_to_async
    def mark_notifications_read(self):
        """
        marks a notification as "read"
        """
        user = self.scope["user"]
        if user.is_authenticated:
            notifications = Notification.objects.filter(target=user)
            if notifications:
                for notification in notifications.all():
                    notification.read = True
                    notification.save()
        return


    @database_sync_to_async
    def get_chat_notifications(self, page_number):
        """
        Get Chat Notifications with Pagination (next page of results).
        This is for appending to the bottom of the notifications list.
        Chat Notifications are:
            1. UnreadChatRoomMessages
        """
        user = self.scope["user"]
        if user.is_authenticated:
            chatmessage_ct = ContentType.objects.get_for_model(UnreadChatRoomMessages)
            notifications = Notification.objects.filter(target=user, content_type=chatmessage_ct).order_by('-timestamp')
            p = Paginator(notifications, DEFAULT_NOTIFICATION_PAGE_SIZE)

            # sleep 1s for testing
            # sleep(1)  
            payload = {}
            if len(notifications) > 0:
                if int(page_number) <= p.num_pages:
                    s = LazyNotificationEncoder()
                    serialized_notifications = s.serialize(p.page(page_number).object_list)
                    payload['notifications'] = serialized_notifications
                    new_page_number = int(page_number) + 1
                    payload['new_page_number'] = new_page_number
                else:
                    return None
        else:
            raise NotificationClientError("User must be authenticated to get notifications.")

        return json.dumps(payload)

    @database_sync_to_async
    def refresh_chat_notifications(self, oldest_timestamp):
        """
        Retrieve the chat notifications newer than the older one on the screen.
        This will accomplish 2 things:
        1. Notifications currently visible will be updated
        2. Any new notifications will be appending to the top of the list
        """
        payload = {}
        user = self.scope["user"]
        if user.is_authenticated:
            timestamp = oldest_timestamp[0:oldest_timestamp.find("+")] # remove timezone because who cares
            timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
            chatmessage_ct = ContentType.objects.get_for_model(UnreadChatRoomMessages)
            notifications = Notification.objects.filter(target=user, content_type=chatmessage_ct, timestamp__gte=timestamp).order_by('-timestamp')

            s = LazyNotificationEncoder()
            payload['notifications'] = s.serialize(notifications)
        else:
            raise NotificationClientError("User must be authenticated to get notifications.")

        return json.dumps(payload)  

    @database_sync_to_async
    def get_unread_general_notification_count(self):
        user = self.scope["user"]
        payload = {}
        if user.is_authenticated:
            friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
            friend_list_ct = ContentType.objects.get_for_model(FriendList)
            notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct])

            
            unread_count = 0
            if notifications:
                for notification in notifications.all():
                    if not notification.read:
                        unread_count = unread_count + 1
            payload['count'] = unread_count
            return json.dumps(payload)
        else:
            raise NotificationClientError("User must be authenticated to get notifications.")
        return None

    @database_sync_to_async
    def get_general_notifications(self, page_number):
        """
        Get General Notifications with Pagination (next page of results).
        This is for appending to the bottom of the notifications list.
        General Notifications are:
            1. FriendRequest
            2. FriendList
        """
        user = self.scope["user"]
        if user.is_authenticated:
            friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
            friend_list_ct = ContentType.objects.get_for_model(FriendList)
            notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct]).order_by('-timestamp')
            p = Paginator(notifications, DEFAULT_NOTIFICATION_PAGE_SIZE)

            # sleep 1s for testing
            # sleep(1)  
            payload = {}
            if len(notifications) > 0:
                if int(page_number) <= p.num_pages:
                    s = LazyNotificationEncoder()
                    serialized_notifications = s.serialize(p.page(page_number).object_list)
                    payload['notifications'] = serialized_notifications
                    new_page_number = int(page_number) + 1
                    payload['new_page_number'] = new_page_number
                else:
                    return None
        else:
            raise NotificationClientError("User must be authenticated to get notifications.")

        return json.dumps(payload)



    @database_sync_to_async
    def refresh_general_notifications(self, oldest_timestamp):
        """
        Retrieve the general notifications newer than the older one on the screen.
        This will accomplish 2 things:
        1. Notifications currently visible will be updated
        2. Any new notifications will be appending to the top of the list
        """
        payload = {}
        user = self.scope["user"]
        if user.is_authenticated:
            timestamp = oldest_timestamp[0:oldest_timestamp.find("+")] # remove timezone because who cares
            timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
            friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
            friend_list_ct = ContentType.objects.get_for_model(FriendList)
            notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct], timestamp__gte=timestamp).order_by('-timestamp')

            s = LazyNotificationEncoder()
            payload['notifications'] = s.serialize(notifications)
        else:
            raise NotificationClientError("User must be authenticated to get notifications.")

        return json.dumps(payload)        












